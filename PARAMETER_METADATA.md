# Parameter metadata

Strategieparameters blijven uit de `hyperparameters()`-definitie komen. Optionele `parameter_metadata.json` in de strategie-map voegt alleen presentatietekst toe; dit bestand wordt niet naar Jesse's parameterpayload doorgestuurd.

## LabBreakout

| Parameter | Label / betekenis | Eenheid en grenzen |
|---|---|---|
| order_mode | Long marktorder / Long stop boven candle-high / Short stop onder candle-low | Enginewaarden exact 0 / 1 / 2 |
| entry_interval | Entry-interval | candles, 10–240 |
| position_size | Ordergrootte | Jesse-quantity, 0.01–10; geen gegarandeerde broker-lot |
| ema_period | EMA-periode | candles, 5–100 |
| max_bar_range | Maximale candle-range | ruwe prijsafstand, 1–100 |
| stop_distance / target_distance | Stop-lossafstand / take-profitafstand | ruwe prijsafstand, 0.1–25 |
| stop_offset | Stop-entry-offset | ruwe prijsafstand, 0.001–5 |
| pending_expiry_bars | Vervallen na | candles, 1–30 |

De uitleg hierboven is gecontroleerd tegen de LabBreakout-broncode. Voor andere strategieën wordt ontbrekende metadata als onbekend gemarkeerd; er wordt geen betekenis afgeleid uit alleen een variabelenaam.

## Browseruitleg — 2026-11-06

Elk beschreven parameterlabel heeft nu een toetsenbordbedienbare `i`-knop. Openen toont de beschrijving, eenheid, grenzen en standaardwaarde uit Jesse's hyperparameterdefinitie; de huidige waarde blijft in het invoerveld zichtbaar. Categorische velden behouden hun numerieke enginewaarde en tonen de beschrijvende optie-labels. Parameters zonder beschrijving blijven met `?` als niet-geverifieerd gemarkeerd. Dit verandert de payload naar Jesse niet.

## Eenvoudige en geavanceerde instellingen — 2026-11-06

`simple` in de metadata is uitsluitend een presentatiewaarde. LabBreakout markeert `order_mode`, `entry_interval`, `position_size` en `ema_period` als kerninstellingen; de overige vijf parameters staan onder de inklapbare sectie **Overige strategieparameters**. Die samenvatting toont de actuele waarde en eenheid. IAB-controle bevestigde dat alle negen velden zichtbaar/bewerkbaar blijven en dat uitklappen de waarden `max_bar_range=25`, `stop_distance=2.5`, `target_distance=1`, `stop_offset=0.1` en `pending_expiry_bars=5` toont. De opgeslagen backtestrun behield deze waarden in het Jesse-payloadmanifest.
