# Horizon Logic Design System

Asset ID: `928c1800009549bdbab9ecb70a32164c`

Source resource: `assets/928c1800009549bdbab9ecb70a32164c`

## Brand & Style

The brand personality is intelligent, efficient, and reliable. The UI should make complex AI planning feel calm, structured, and actionable.

The design direction is corporate / modern with functional layering. It prioritizes information density, map data, itinerary readability, and a workbench-style layout.

## Layout

- Desktop: multi-pane workbench layout.
- Mobile: single-column stream layout.
- Day-based itinerary sections are separated clearly.
- Spacing follows an 8px base grid.

## Typography

- Headline font: Hanken Grotesk.
- Body font: Inter.
- Data font: JetBrains Mono.

## Colors

- Primary: `#1890ff`
- Secondary: `#00b2a9`
- Neutral: `#64748b`
- Map polyline: `#1890ff`
- Map marker: `#f5222d`
- Weather sunny: `#faad14`
- Weather rain: `#4fa1ff`
- Empty surface: `#f1f5f9`

## Shape

- Base roundness: 8px.
- Cards can use larger rounded containers.
- Buttons and inputs keep a professional 8px radius.

## Components

### AI Chat Interface

- Supports SSE streaming.
- Input is a multiline text area.
- Send button enters disabled state during active generation.

### Itinerary Cards

- Grouped by day.
- Each day includes a header, map zone, and attraction list.
- Cards may support expand/collapse in later versions.

### Map

- Primary blue for active routes.
- Secondary teal for candidate spots.
- Minimal markers with high contrast.

### Empty States

- Use low-contrast typography.
- Include clear next-step guidance.

