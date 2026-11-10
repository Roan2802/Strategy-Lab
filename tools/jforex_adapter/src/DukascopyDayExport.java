import com.dukascopy.api.Filter;
import com.dukascopy.api.IAccount;
import com.dukascopy.api.IBar;
import com.dukascopy.api.IContext;
import com.dukascopy.api.IHistory;
import com.dukascopy.api.IMessage;
import com.dukascopy.api.IStrategy;
import com.dukascopy.api.ITick;
import com.dukascopy.api.Instrument;
import com.dukascopy.api.JFException;
import com.dukascopy.api.OfferSide;
import com.dukascopy.api.Period;
import com.dukascopy.api.system.ClientFactory;
import com.dukascopy.api.system.IClient;
import com.dukascopy.api.system.ISystemListener;
import com.dukascopy.api.system.JFAuthenticationException;
import com.dukascopy.api.system.JFVersionException;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.File;
import java.io.IOException;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Read-only export of ONE UTC day of Dukascopy M1 bid+ask candles for ONE instrument.
 *
 * Usage: DukascopyDayExport SYMBOL YYYY-MM-DD STAGING_DIR | --self-test
 * Exit codes: 0 ok, 2 day not available yet, 3 auth/config, 4 network/transient, 5 unknown instrument.
 *
 * Candle policy: history is requested with Filter.WEEKENDS (JForex drops flat bars for weekends, holidays and market
 * breaks; Filter.ALL_FLATS is a deprecated alias of it). Bars are additionally dropped when both sides have volume 0,
 * so no candle is invented. Output timestamps are UTC minute OPEN times.
 */
public final class DukascopyDayExport {
    static final int EXIT_OK = 0, EXIT_NOT_READY = 2, EXIT_CONFIG = 3, EXIT_NETWORK = 4, EXIT_UNKNOWN = 5;
    static final long MINUTE = 60_000L, DAY = 86_400_000L, CLOSED_MARKET_GRACE = 3 * 3_600_000L;
    static final String HEADER = "time_utc,open,high,low,close,volume";
    static final int TIMEOUT_SECONDS = 120;

    static final Map<String, String> INSTRUMENTS = new LinkedHashMap<>();
    static final Map<String, String> ALIASES = new LinkedHashMap<>();
    static {
        INSTRUMENTS.put("XAUUSD", "XAU/USD");
        INSTRUMENTS.put("EURUSD", "EUR/USD");
        INSTRUMENTS.put("GBPUSD", "GBP/USD");
        INSTRUMENTS.put("USDJPY", "USD/JPY");
        INSTRUMENTS.put("USA30IDXUSD", "USA30.IDX/USD");
        INSTRUMENTS.put("USA500IDXUSD", "USA500.IDX/USD");
        INSTRUMENTS.put("USATECHIDXUSD", "USATECH.IDX/USD");
        INSTRUMENTS.put("DEUIDXEUR", "DEU.IDX/EUR");
        INSTRUMENTS.put("BTCUSD", "BTC/USD");
        ALIASES.put("US30", "USA30IDXUSD");
        ALIASES.put("US500", "USA500IDXUSD");
        ALIASES.put("USTEC", "USATECHIDXUSD");
        ALIASES.put("DE40", "DEUIDXEUR");
    }

    record Bar(long time, double open, double high, double low, double close, double volume) {}

    static void log(String message) {
        System.err.println("[jforex] " + message);
    }

    static Instrument resolve(String symbol) {
        String key = symbol.toUpperCase();
        key = ALIASES.getOrDefault(key, key);
        String name = INSTRUMENTS.get(key);
        return name == null ? null : Instrument.fromString(name);
    }

    // ---------------------------------------------------------------- output

    static String number(double value) {
        return BigDecimal.valueOf(value).stripTrailingZeros().toPlainString();
    }

    static void writeAtomic(Path target, List<Bar> bars) throws IOException {
        Path temp = target.resolveSibling(target.getFileName() + ".tmp");
        StringBuilder text = new StringBuilder(HEADER).append('\n');
        for (Bar bar : bars) {
            text.append(Instant.ofEpochMilli(bar.time()).toString().replaceFirst("\\.\\d+Z$", "Z"))
                .append(',').append(number(bar.open())).append(',').append(number(bar.high()))
                .append(',').append(number(bar.low())).append(',').append(number(bar.close()))
                .append(',').append(number(bar.volume())).append('\n');
        }
        Files.writeString(temp, text, StandardCharsets.UTF_8);
        Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }

    /** Writes the pair; the BID file is renamed first, a failed ASK write removes it again. */
    static void writePair(Path directory, String symbol, LocalDate day, List<Bar> bid, List<Bar> ask) throws IOException {
        Files.createDirectories(directory);
        Path bidPath = directory.resolve(symbol + "_" + day + "_BID.csv");
        Path askPath = directory.resolve(symbol + "_" + day + "_ASK.csv");
        writeAtomic(bidPath, bid);
        try {
            writeAtomic(askPath, ask);
        } catch (IOException | RuntimeException e) {
            Files.deleteIfExists(bidPath);
            throw e;
        }
    }

    /** Keeps minutes of the day on which at least one side traded; null when the two sides disagree on the minutes. */
    static List<List<Bar>> clean(List<Bar> bid, List<Bar> ask, long dayStart) {
        TreeMap<Long, Bar> bids = index(bid, dayStart), asks = index(ask, dayStart);
        if (!bids.keySet().equals(asks.keySet())) {
            return null;
        }
        List<Bar> outBid = new ArrayList<>(), outAsk = new ArrayList<>();
        for (Map.Entry<Long, Bar> entry : bids.entrySet()) {
            Bar other = asks.get(entry.getKey());
            if (entry.getValue().volume() > 0 || other.volume() > 0) {
                outBid.add(entry.getValue());
                outAsk.add(other);
            }
        }
        return List.of(outBid, outAsk);
    }

    private static TreeMap<Long, Bar> index(List<Bar> bars, long dayStart) {
        TreeMap<Long, Bar> map = new TreeMap<>();
        for (Bar bar : bars) {
            if (bar.time() >= dayStart && bar.time() < dayStart + DAY) {
                map.put(bar.time(), bar);
            }
        }
        return map;
    }

    // ---------------------------------------------------------------- credentials

    record Credentials(String jnlp, String username, String password) {}

    static Path credentialsPath() {
        String env = System.getenv("JFOREX_CREDENTIALS");
        if (env != null && !env.isBlank()) {
            return Paths.get(env);
        }
        // run.cmd sets jforex.root to the project root; fall back to the working directory.
        return Paths.get(System.getProperty("jforex.root", "."), "strategy_lab", "jforex_credentials.local.json");
    }

    static Credentials loadCredentials() {
        Path path = credentialsPath().normalize();
        if (!Files.isRegularFile(path)) {
            log("Inloggegevens ontbreken: " + path + " bestaat niet. Kopieer strategy_lab/jforex_credentials.example.json "
                + "naar strategy_lab/jforex_credentials.local.json en vul je Dukascopy demo-login in "
                + "(of zet JFOREX_CREDENTIALS op het pad van je bestand).");
            return null;
        }
        try {
            JsonObject json = JsonParser.parseString(Files.readString(path, StandardCharsets.UTF_8)).getAsJsonObject();
            String jnlp = json.get("jnlp_url").getAsString().trim();
            String user = json.get("username").getAsString().trim();
            String pass = json.get("password").getAsString();
            if (jnlp.isEmpty() || user.isEmpty() || pass.isEmpty()) {
                throw new IllegalArgumentException("empty value");
            }
            return new Credentials(jnlp, user, pass);
        } catch (Exception e) {
            log("Inloggegevensbestand " + path + " is onleesbaar of onvolledig: vereist zijn de velden jnlp_url, username "
                + "en password (zie jforex_credentials.example.json).");
            return null;
        }
    }

    // ---------------------------------------------------------------- strategy (history reads only)

    static final class ReadOnlyHistoryStrategy implements IStrategy {
        final Instrument instrument;
        final long from, to;
        final CountDownLatch done = new CountDownLatch(1);
        volatile List<Bar> bid, ask;
        volatile long lastTickTime = -1;
        volatile Throwable failure;

        ReadOnlyHistoryStrategy(Instrument instrument, long from, long to) {
            this.instrument = instrument;
            this.from = from;
            this.to = to;
        }

        @Override public void onStart(IContext context) {
            try {
                IHistory history = context.getHistory();
                lastTickTime = history.getTimeOfLastTick(instrument);
                bid = fetch(history, OfferSide.BID);
                ask = fetch(history, OfferSide.ASK);
            } catch (Throwable t) {
                failure = t;
            } finally {
                done.countDown();
            }
        }

        private List<Bar> fetch(IHistory history, OfferSide side) throws JFException {
            List<Bar> out = new ArrayList<>();
            for (IBar bar : history.getBars(instrument, Period.ONE_MIN, side, Filter.WEEKENDS, from, to)) {
                out.add(new Bar(bar.getTime(), bar.getOpen(), bar.getHigh(), bar.getLow(), bar.getClose(), bar.getVolume()));
            }
            return out;
        }

        @Override public void onTick(Instrument i, ITick tick) {}
        @Override public void onBar(Instrument i, Period p, IBar a, IBar b) {}
        @Override public void onMessage(IMessage m) {}
        @Override public void onAccount(IAccount a) {}
        @Override public void onStop() {}
    }

    // ---------------------------------------------------------------- main flow

    static int export(String symbol, LocalDate day, Path staging) {
        Instrument instrument = resolve(symbol);
        if (instrument == null) {
            log("Onbekend instrument: " + symbol);
            return EXIT_UNKNOWN;
        }
        String providerSymbol = ALIASES.getOrDefault(symbol.toUpperCase(), symbol.toUpperCase());
        if (!day.isBefore(LocalDate.now(ZoneOffset.UTC))) {
            log("Dag " + day + " is nog niet afgesloten (UTC); niets geschreven.");
            return EXIT_NOT_READY;
        }
        Credentials credentials = loadCredentials();
        if (credentials == null) {
            return EXIT_CONFIG;
        }
        long dayStart = day.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli();
        long dayEnd = dayStart + DAY;

        IClient client;
        try {
            client = ClientFactory.getDefaultInstance();
            String cache = System.getProperty("jforex.cache");
            if (cache != null) {
                Files.createDirectories(Paths.get(cache));
                client.setCacheDirectory(new File(cache));
            }
        } catch (Exception e) {
            log("JForex-client kon niet worden gemaakt: " + e.getClass().getSimpleName());
            return EXIT_CONFIG;
        }
        CountDownLatch connected = new CountDownLatch(1);
        client.setSystemListener(new ISystemListener() {
            @Override public void onStart(long processId) {}
            @Override public void onStop(long processId) {}
            @Override public void onConnect() { connected.countDown(); }
            @Override public void onDisconnect() {}
        });
        try {
            try {
                client.connect(credentials.jnlp(), credentials.username(), credentials.password());
            } catch (JFAuthenticationException | JFVersionException e) {
                log("Inloggen mislukt (" + e.getClass().getSimpleName() + "). Controleer demo-login en jnlp_url.");
                return EXIT_CONFIG;
            } catch (Exception e) {
                log("Verbinden mislukt: " + e.getClass().getSimpleName());
                return EXIT_NETWORK;
            }
            if (!connected.await(30, TimeUnit.SECONDS) && !client.isConnected()) {
                log("Geen verbinding binnen 30 s.");
                return EXIT_NETWORK;
            }
            if (!client.getAvailableInstruments().contains(instrument)) {
                log("Instrument niet beschikbaar op dit account: " + providerSymbol);
                return EXIT_UNKNOWN;
            }
            Set<Instrument> wanted = new HashSet<>(List.of(instrument));
            client.setSubscribedInstruments(wanted);
            for (int i = 0; i < 100 && !client.getSubscribedInstruments().contains(instrument); i++) {
                Thread.sleep(100);
            }
            ReadOnlyHistoryStrategy strategy = new ReadOnlyHistoryStrategy(instrument, dayStart, dayEnd - MINUTE);
            long processId = client.startStrategy(strategy);
            boolean finished = strategy.done.await(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            client.stopStrategy(processId);
            if (!finished || strategy.failure != null || strategy.bid == null || strategy.ask == null) {
                log("Geschiedenis ophalen mislukt"
                    + (strategy.failure == null ? "" : ": " + strategy.failure.getClass().getSimpleName()));
                return EXIT_NETWORK;
            }
            boolean providerPastDay = strategy.lastTickTime >= dayEnd - MINUTE;
            boolean closedMarketSettled = System.currentTimeMillis() >= dayEnd + CLOSED_MARKET_GRACE;
            if (!providerPastDay && !closedMarketSettled) {
                log("Provider heeft nog geen data tot einde van " + day + "; niets geschreven.");
                return EXIT_NOT_READY;
            }
            List<List<Bar>> cleaned = clean(strategy.bid, strategy.ask, dayStart);
            if (cleaned == null) {
                log("Bid- en ask-minuten komen niet overeen; later opnieuw proberen.");
                return EXIT_NETWORK;
            }
            if (cleaned.get(0).isEmpty()) {
                log("Geen handelsminuten gevonden voor " + providerSymbol + " op " + day + "; niets geschreven.");
                return EXIT_NOT_READY;
            }
            writePair(staging, providerSymbol, day, cleaned.get(0), cleaned.get(1));
            log("OK " + providerSymbol + " " + day + ": " + cleaned.get(0).size() + " minuten (bid+ask).");
            return EXIT_OK;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return EXIT_NETWORK;
        } catch (IOException e) {
            log("Schrijven naar " + staging + " mislukt: " + e.getClass().getSimpleName());
            return EXIT_NETWORK;
        } catch (Exception e) {
            log("Onverwachte fout: " + e.getClass().getSimpleName());
            return EXIT_NETWORK;
        } finally {
            try {
                client.disconnect();
            } catch (Throwable ignored) {
            }
        }
    }

    // ---------------------------------------------------------------- self test

    static int selfTest() {
        int failures = 0;
        for (Map.Entry<String, String> entry : INSTRUMENTS.entrySet()) {
            Instrument instrument = resolve(entry.getKey());
            boolean ok = instrument != null && entry.getValue().equals(instrument.toString());
            System.err.println((ok ? "PASS " : "FAIL ") + "instrument " + entry.getKey() + " -> " + instrument);
            failures += ok ? 0 : 1;
        }
        for (Map.Entry<String, String> entry : ALIASES.entrySet()) {
            Instrument viaAlias = resolve(entry.getKey()), direct = resolve(entry.getValue());
            boolean ok = viaAlias != null && viaAlias.equals(direct);
            System.err.println((ok ? "PASS " : "FAIL ") + "alias " + entry.getKey());
            failures += ok ? 0 : 1;
        }
        failures += check(resolve("NOPE") == null, "unknown symbol rejected");
        try {
            failures += selfTestOutput();
        } catch (Exception e) {
            System.err.println("FAIL output test: " + e);
            failures++;
        }
        System.err.println(failures == 0 ? "SELF-TEST OK" : "SELF-TEST FAILED (" + failures + ")");
        return failures == 0 ? EXIT_OK : 1;
    }

    private static int selfTestOutput() throws IOException {
        LocalDate day = LocalDate.of(2026, 11, 6);
        long start = day.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli();
        List<Bar> bid = new ArrayList<>(), ask = new ArrayList<>();
        bid.add(new Bar(start - MINUTE, 9, 9, 9, 9, 1));
        ask.add(new Bar(start - MINUTE, 9, 9, 9, 9, 1));
        bid.add(new Bar(start, 2650.5, 2651.25, 2650.0, 2651.0, 12.5));
        ask.add(new Bar(start, 2650.9, 2651.65, 2650.4, 2651.4, 11.0));
        bid.add(new Bar(start + MINUTE, 2651.0, 2651.0, 2651.0, 2651.0, 0));
        ask.add(new Bar(start + MINUTE, 2651.4, 2651.4, 2651.4, 2651.4, 0));
        bid.add(new Bar(start + 2 * MINUTE, 1.0E-5, 2.0, 1.0E-5, 1.5, 0.0003));
        ask.add(new Bar(start + 2 * MINUTE, 1.0E-5, 2.0, 1.0E-5, 1.5, 0.0004));
        bid.add(new Bar(start + DAY - MINUTE, 3, 3, 3, 3, 1));
        ask.add(new Bar(start + DAY - MINUTE, 3, 3, 3, 3, 1));
        List<List<Bar>> cleaned = clean(bid, ask, start);
        Path dir = Files.createTempDirectory("jforex-selftest-");
        int failures = 0;
        try {
            failures += check(cleaned != null && cleaned.get(0).size() == 3, "flat and out-of-day bars dropped");
            failures += check(clean(bid, ask.subList(0, 2), start) == null, "mismatched sides rejected");
            writePair(dir, "XAUUSD", day, cleaned.get(0), cleaned.get(1));
            List<String> lines = Files.readAllLines(dir.resolve("XAUUSD_2026-11-06_BID.csv"));
            failures += check(lines.get(0).equals(HEADER), "header");
            failures += check(lines.get(1).equals("2026-11-06T00:00:00Z,2650.5,2651.25,2650,2651,12.5"),
                "row format: " + lines.get(1));
            failures += check(lines.get(2).equals("2026-11-06T00:02:00Z,0.00001,2,0.00001,1.5,0.0003"),
                "plain decimals: " + lines.get(2));
            failures += check(lines.size() == 4, "row count");
            failures += check(Files.readAllLines(dir.resolve("XAUUSD_2026-11-06_ASK.csv")).size() == 4, "ask row count");
            try (var files = Files.list(dir)) {
                failures += check(files.noneMatch(p -> p.toString().endsWith(".tmp")), "no tmp files left");
            }
        } finally {
            try (var files = Files.list(dir)) {
                for (Path p : (Iterable<Path>) files::iterator) {
                    Files.deleteIfExists(p);
                }
            }
            Files.deleteIfExists(dir);
        }
        return failures;
    }

    private static int check(boolean ok, String name) {
        System.err.println((ok ? "PASS " : "FAIL ") + name);
        return ok ? 0 : 1;
    }

    // ---------------------------------------------------------------- entry

    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("--self-test")) {
            System.err.flush();
            Runtime.getRuntime().halt(selfTest());
        }
        if (args.length != 3) {
            log("Gebruik: DukascopyDayExport SYMBOOL JJJJ-MM-DD STAGINGMAP");
            System.exit(EXIT_CONFIG);
        }
        LocalDate day;
        try {
            day = LocalDate.parse(args[1]);
        } catch (Exception e) {
            log("Ongeldige datum: " + args[1]);
            System.exit(EXIT_CONFIG);
            return;
        }
        Thread watchdog = new Thread(() -> {
            try {
                Thread.sleep(TIMEOUT_SECONDS * 1000L);
            } catch (InterruptedException e) {
                return;
            }
            log("Time-out na " + TIMEOUT_SECONDS + " s.");
            Runtime.getRuntime().halt(EXIT_NETWORK);
        }, "watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
        int code = export(args[0], day, Paths.get(args[2]));
        System.err.flush();
        Runtime.getRuntime().halt(code);
    }
}
