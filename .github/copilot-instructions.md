# Copilot Instructions — India Votes Data

## Design Context

### Users
Citizens, journalists, researchers, and data enthusiasts tracking Indian election results in real time. They arrive on counting day with high attention and low patience. The dashboard must deliver instant clarity — who's winning, where the swing is, how counting is progressing — without requiring any learning curve.

### Brand Personality
**Serious, informative, clean.** This is a public-interest data tool, not a news product. It speaks with the quiet authority of raw numbers, not the manufactured urgency of a news ticker. Think: a well-designed census report that happens to update live.

### Aesthetic Direction
**Greyscale-first with colour as signal, not decoration.** The UI uses a sophisticated three-theme system to accommodate different reading environments while maintaining a disciplined, data-forward aesthetic.

#### Themes
1.  **black-white (Primary Light)**
    *   **Vibe**: High-contrast, editorial, "Clear as Day."
    *   **Palette**: Pure white (`#ffffff`), black text (`#000000`), sharp greyscale borders.
    *   **Typography**: Libre Franklin (UI/Headings) + Inconsolata (Numeric).
    *   **Role**: The default light mode for maximum legibility.

2.  **fair-lovely (Light - Editorial)**
    *   **Vibe**: Warm, paper-like, "Scale me Grey."
    *   **Palette**: Warm ivory background (`#f3f0e8`), muted slate accents (`#556475`).
    *   **Typography**: Source Sans 3 (UI/Headings) + IBM Plex Mono (Numeric).
    *   **Role**: A softer alternative for extended reading or lower-light environments.

3.  **dark-handsome (Dark)**
    *   **Vibe**: Professional, serious, "Colour me Surprised!"
    *   **Palette**: Deep grey background (`#333333`), crisp white text (`#f6f6f6`).
    *   **Typography**: IBM Plex Sans (UI/Headings) + IBM Plex Mono (Numeric).
    *   **Role**: The primary dark mode.

#### Core Aesthetic Rules
*   **Colour = Signal**: Chromatic colour (party reds, greens, yellows) enters ONLY to differentiate political parties or status (Live/Counting/Pending). Never use colour for backgrounds, decorations, or non-data elements.
*   **Anti-Reference**: No screaming banners, no flashing tickers, no visual noise, no gradients, no glows.
*   **Emotional Goal**: The excitement of watching live TV as results update smoothly — controlled tension, not chaos.

### Design Principles
1.  **Colour = Signal, Nothing Else.** Greyscale UI. Party colours appear only in data visualisations (bar charts, trend lines, seat tally). Never use colour for decoration, backgrounds, or non-data elements.
2.  **Serious Over Spectacular.** No gradients on cards, no glows, no animations for their own sake. The data itself is dramatic enough. The UI should recede and let the numbers speak.
3.  **Density Without Clutter.** Show maximum information per viewport, but with disciplined spacing, typographic hierarchy, and clear visual grouping. Every pixel earns its place. Max width: 1200px.
4.  **Live-TV Feel.** Smooth updates, no jarring re-renders. Status changes should feel like a natural progression, not a page refresh. Auto-refresh is invisible tension-building, not disruptive.
5.  **Accessible by Default.** Greyscale palette naturally avoids colour-blindness issues. Party colours are only meaningful in context (charts with labels), never the sole carrier of meaning. Text contrast ratios meet WCAG AA. High-contrast focus rings (`#000000` or `#93c5fd`) for keyboard navigation.

## Technical Notes
*   **Stack**: FastAPI (Python) + Chart.js (Frontend).
*   **State Management**: `localStorage` for theme persistence.
*   **Layout**: Single-page layout, `width: min(100%, 1200px)`, centered.
*   **UI Chrome**: Sticky headers with `backdrop-filter: blur(12px)` for depth without weight.
*   **Data Density**: High-density numeric layout using `tabular-nums` and `lining-nums` for perfect alignment.
*   **Status Indicators**:
    *   `Live`: Pulsing green dot.
    *   `Counting`: Striped yellow background (diagonal CSS gradient).
    *   `Pending`: Solid red dot.
*   **Party Colours**: Defined in the JS `PARTY_COLORS` dictionary; mapped to CSS variables per theme for optimal contrast.
