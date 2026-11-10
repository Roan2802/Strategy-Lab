# Layout-editor

1. Kies bovenaan een ingebouwd profiel of een eerder opgeslagen profiel.
2. Klik **Layout bewerken** om panelen te slepen, te verbergen of terug te halen.
3. Versleep de verticale scheidingslijnen voor breedte. Gebruik links/rechts om de gefocuste splitter met 12 px te veranderen.
4. Sleep de horizontale greep onder de chart om de hoogte (480–1200 px) aan te passen. Met focus veranderen Omhoog/Omlaag 12 px en PageUp/PageDown 100 px. **Chartfocus** brengt de chart in beeld; het vierkant opent fullscreen.
5. Geef de layout een naam en kies **Layout opslaan**. De app bewaart layouts onder `strategy_lab/layouts/layouts.json`, zodat browserprofielen dezelfde layouts kunnen gebruiken.
6. **Annuleren** herstelt de layout zoals die was toen de editor openging. **Standaard herstellen** zet de standaardindeling terug.

De server valideert schema-versie, panelnamen en afmetingen. Een corrupte of oude JSON geeft een lege herstelbare lijst terug; de app overschrijft die pas na een expliciete save.

## Verified pointer drag and toolbar controls — 2026-11-06

In Edit Mode, drag a handle (⠿) on Settings, Chart or Inspector onto another main panel. A highlighted before/after edge shows the drop position; a 8 px movement threshold avoids treating a click as a drag. Drag is enabled only in Edit Mode. Results remains in the bottom row. Use the arrows for keyboard-accessible reordering. Splitters continue to resize widths/heights separately.

Run selection, refresh, Backtest uitvoeren and Optimaliseren are in the global toolbar. The Backtest action was exercised in the ChatGPT IAB and completed a one-day Dukascopy XAUUSD M1 test (13 trades, 1,432 events). Layout profiles remain central at `strategy_lab/layouts/layouts.json`; select a saved profile in the profile dropdown in each browser. Current cross-browser proof covers only separate ChatGPT IAB tabs; Chrome/Edge remain unverified.
