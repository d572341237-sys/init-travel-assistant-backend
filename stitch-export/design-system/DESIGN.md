---
name: Horizon Logic
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#404753'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#707785'
  outline-variant: '#c0c7d6'
  surface-tint: '#005fae'
  primary: '#005daa'
  on-primary: '#ffffff'
  primary-container: '#0075d5'
  on-primary-container: '#fefcff'
  inverse-primary: '#a5c8ff'
  secondary: '#006a64'
  on-secondary: '#ffffff'
  secondary-container: '#6ef4ea'
  on-secondary-container: '#006f69'
  tertiary: '#934600'
  on-tertiary: '#ffffff'
  tertiary-container: '#b95a00'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d4e3ff'
  primary-fixed-dim: '#a5c8ff'
  on-primary-fixed: '#001c3a'
  on-primary-fixed-variant: '#004785'
  secondary-fixed: '#72f7ed'
  secondary-fixed-dim: '#50dad1'
  on-secondary-fixed: '#00201e'
  on-secondary-fixed-variant: '#00504b'
  tertiary-fixed: '#ffdbc7'
  tertiary-fixed-dim: '#ffb688'
  on-tertiary-fixed: '#311300'
  on-tertiary-fixed-variant: '#733600'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
  map-polyline: '#1890ff'
  map-marker: '#f5222d'
  weather-sunny: '#faad14'
  weather-rain: '#4fa1ff'
  surface-empty: '#f1f5f9'
typography:
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
  headline-lg-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '700'
    lineHeight: 32px
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-caps:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
  data-mono:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 16px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  stack-sm: 8px
  stack-md: 16px
  stack-lg: 24px
  gutter: 16px
  margin-desktop: 32px
  margin-mobile: 16px
  max-width: 1440px
---

## Brand & Style

The brand personality is **Intelligent, Efficient, and Reliable**. As a Smart Travel Assistant, the design system must bridge the gap between complex AI processing and a seamless, stress-free travel experience. The UI should evoke a sense of calm and clarity, transforming open-ended natural language into structured, actionable itineraries.

The chosen design style is **Corporate / Modern** with a focus on **Functional Layering**. This approach prioritizes information density and data visualization while maintaining a clean, professional aesthetic. It utilizes crisp layouts, purposeful whitespace, and a "workbench" philosophy that makes the user feel in total control of their journey. The interface remains unobtrusive, allowing the travel content and map data to take center stage.

## Colors

The palette is anchored by a **Trust-Evoking Blue** as the primary color, signaling reliability and technological precision. A **Vibrant Travel Teal** serves as the secondary color, used for secondary actions and travel-specific highlights (like weather or destination tags) to inject energy into the professional framework.

- **Primary**: Used for main CTAs, active states, and primary navigation elements.
- **Secondary**: Used for supplemental information tags, "vibe" indicators, and secondary buttons.
- **Neutral**: A sophisticated slate gray used for typography, borders, and UI scaffolding.
- **Functional Colors**: Specific tokens are reserved for map visualizations—ensuring high legibility against varied map tiles—and weather status indicators to provide immediate cognitive recognition of environmental conditions.

## Typography

The typography system is designed for high-speed scanning of information-dense itineraries. 

- **Headlines**: Use **Hanken Grotesk** for a sharp, contemporary feel that communicates modern intelligence.
- **Body**: Use **Inter** for its exceptional readability and neutral tone, ensuring that long itinerary descriptions remain legible across all devices.
- **Technical Data**: Use **JetBrains Mono** for distances, time windows, and coordinates. This monospaced font provides a "data-rich" aesthetic that helps users distinguish logistics from descriptions.

On mobile, typography scales to prevent horizontal overflow. All body text is set to auto-wrap, and large headlines are reduced in size to maintain visual hierarchy without consuming the limited vertical viewport.

## Layout & Spacing

This design system utilizes a **Fluid Grid** approach that adapts between a multi-pane "Workbench" on desktop and a vertical "Stream" on mobile.

- **Desktop**: A three-pane layout featuring a persistent Input/Chat area (Left), an interactive Itinerary/Map area (Center/Right), and fly-out Drawers for history and settings.
- **Mobile**: A single-column flow. The map is pinned to a fixed height (240px-280px) within itinerary cards to ensure context is never lost while scrolling.
- **Rhythm**: A strict 8px base grid drives all spacing. Sections are "Partitioned by Day," using significant vertical stacking (`stack-lg`) to create clear mental breaks between different parts of the trip.

## Elevation & Depth

Visual hierarchy is conveyed through **Tonal Layers** and **Ambient Shadows**. This design system avoids unnecessary skeuomorphism, opting for depth that clarifies the interface's structure.

- **Surface Levels**: The base application uses a subtle off-white background. Primary content containers (Cards) use pure white with a very soft, diffused shadow to appear "lifted."
- **Drawers & Modals**: Secondary features like "History" or "Profile" use high-elevation shadows and semi-opaque backdrops to focus the user's attention on the temporary task.
- **Interactive States**: Buttons and input fields use subtle inner shadows on hover to provide tactile feedback without breaking the clean, flat aesthetic.

## Shapes

The shape language is **Rounded**, utilizing a 0.5rem (8px) base radius. This softens the "technical" nature of the AI-driven data, making the assistant feel more approachable and friendly. 

- **Cards & Panes**: Use the standard `rounded-lg` (16px) for a soft, modern container feel.
- **Interactive Elements**: Buttons and input fields use the base 8px radius to maintain a professional, structured look.
- **Map Markers**: Custom markers utilize a "Pin" shape with slightly rounded top-corners to blend the map's geometric utility with the overall system aesthetic.

## Components

### AI Chat Interface
The chat interface must support **SSE Streaming**. Text should render token-by-token with a subtle fade-in animation. The input box is a multi-line text area that expands as the user types, with a prominent "Send" button that enters a disabled, grayed-out state during active AI generations.

### Card-based Itineraries
Itinerary items are grouped into "Day Cards." Each card contains a header (e.g., "Day 1"), an embedded Gaode Map zone, and a vertical list of attractions. Cards should be treated as cohesive units that can be expanded or collapsed.

### Status Indicators & Icons
- **Weather**: Use a consistent set of minimalist line icons. Include a "vibe" tag (e.g., "Perfect for walking") in the secondary teal color.
- **Map Markers**: Use primary blue for the current route and secondary teal for "Candidate Spots."
- **Empty States**: Use a dedicated `surface-empty` background with centered, low-contrast typography and a simple icon to guide the user when no data is present.

### Buttons & Inputs
Buttons on mobile must allow for label wrapping to prevent layout breakage. Inputs should have clear focus states using the primary blue for the border and a soft glow.