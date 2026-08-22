# AI Stock Scanner — UI Guide

## Architecture

### Layout System

All authenticated pages extend `layouts/shell.html`, which provides:
- **Collapsible sidebar** (`components/sidebar.html`) — 260px expanded / 72px collapsed
- **Sticky topbar** (`components/topbar.html`) — 56px height with theme toggle and user menu
- **Content area** — `{% block content %}` for page-specific content
- **Mobile offcanvas** — responsive sidebar for mobile viewports

Login page extends `base.html` directly (no sidebar/topbar).

### Template Hierarchy

```
base.html                  ← Bootstrap 5.3.3, HTMX 1.9.12, auth.css, shell.js
├── layouts/shell.html     ← sidebar + topbar + content wrapper
│   ├── dashboard.html
│   ├── stocks.html
│   ├── scanner_*.html
│   ├── alerts.html
│   ├── notifications/*.html
│   ├── admin/*.html
│   ├── backtests/*.html
│   └── ...
└── auth/login.html        ← standalone centered card
```

### CSS Architecture

| File | Purpose |
|------|---------|
| `design-system.css` | CSS custom properties, base component styles |
| `dashboard.css` | Shell layout: sidebar, topbar, content grid |
| `auth.css` | Login page centered card |
| `backtests.css` | Backtest-specific styles |
| `notifications.css` | Notification-specific styles |

All CSS files use design system variables for colors, spacing, typography.

### JavaScript

| File | Purpose |
|------|---------|
| `shell.js` | Sidebar collapse, theme toggle, tooltip init |
| `dashboard.js` | Dashboard refresh, HTMX error logging |
| `backtests.js` | Backtest workspace interactions |
| `notifications.js` | Notification preferences, summary scheduler |

## Design System

### CSS Variables (design-system.css)

**Layout:**
- `--sidebar-width: 260px` — expanded sidebar width
- `--sidebar-collapsed-width: 72px` — collapsed sidebar width
- `--topbar-height: 56px` — top bar height

**Colors (light theme):**
- `--color-bg: #f4f6f9` — page background
- `--color-surface: #ffffff` — card/panel background
- `--color-sidebar-bg: #1a1d21` — dark sidebar background
- `--color-text-primary: #1a202c` — primary text
- `--color-text-secondary: #64748b` — secondary text
- `--color-border: #e2e8f0` — borders

**Colors (dark theme):**
- Activated via `[data-bs-theme="dark"]` selector
- All colors remapped for dark backgrounds

**Status:**
- `--color-success: #22c55e`
- `--color-warning: #f59e0b`
- `--color-danger: #ef4444`
- `--color-info: #3b82f6`

### Component Classes

**KPI Cards:**
```html
<div class="kpi-card">
    <div class="kpi-icon" style="background:rgba(59,130,246,0.1);color:var(--color-info);">
        <i class="bi bi-speedometer2"></i>
    </div>
    <div>
        <div class="kpi-label">Label</div>
        <div class="kpi-value">Value</div>
        <div class="kpi-detail">Detail text</div>
    </div>
</div>
```

**Page Headers:**
```html
<div class="page-header">
    <div>
        <h1>Title</h1>
        <p class="page-subtitle">Description</p>
    </div>
    <div class="d-flex gap-2"><!-- actions --></div>
</div>
```

**Badges:**
```html
<span class="badge badge-success-subtle">Success</span>
<span class="badge badge-warning-subtle">Warning</span>
<span class="badge badge-danger-subtle">Danger</span>
<span class="badge badge-info-subtle">Info</span>
<span class="badge badge-neutral-subtle">Neutral</span>
```

**Empty State:**
```html
<div class="empty-state">
    <i class="bi bi-inbox"></i>
    <p>No data available</p>
</div>
```

**Status Dots:**
```html
<span class="status-dot status-dot-success"></span>
<span class="status-dot status-dot-warning"></span>
<span class="status-dot status-dot-danger"></span>
```

## Dark Mode

### How It Works

1. **Theme storage:** `localStorage` key `aistockscanner_theme` stores `light`/`dark`/`system`
2. **Application:** `shell.js` sets `data-bs-theme` attribute on `<body>`
3. **CSS response:** `design-system.css` has `[data-bs-theme="dark"]` block with remapped colors
4. **System detection:** When theme is `system`, follows OS `prefers-color-scheme`

### Theme Toggle

The topbar theme toggle cycles: **Light → Dark → System → Light**

Icon changes:
- Light: moon icon (`bi-moon`)
- Dark: sun icon (`bi-sun`)

### Adding Dark Mode to New Components

Use the existing CSS variables. Example:
```css
.my-component {
    background: var(--color-surface);
    color: var(--color-text-primary);
    border: 1px solid var(--color-border);
}
```

## Navigation

### Sidebar Groups

| Group | Links |
|-------|-------|
| Main | Dashboard |
| Market | Market Data, Market Intelligence*, Scanners, Watchlists |
| Analytics | Stock Analysis, Scanner Results, Backtesting* |
| Monitoring | Alerts, Notifications |
| System | Pipeline, Scheduler, Analytics, Indicators, AI Insights |
| Admin* | Users, Roles & Permissions |

*Requires specific permissions (see below)

### Permission Gating

- **Market Intelligence:** Requires `market_intelligence.read` permission or superuser
- **Backtesting:** Requires `backtests.read` permission or superuser
- **Admin section:** Requires `super_admin` role or superuser flag

### Active State

The sidebar determines active state from `request.url.path`:
```jinja
{% set cp = request.url.path %}
<a href="/dashboard" class="sidebar-link {{ 'active' if cp in ['/', '/dashboard'] else '' }}">
```

## Adding a New Page

1. Create template extending `layouts/shell.html`:
```jinja
{% extends "layouts/shell.html" %}
{% block title %}My Page - AI Stock Scanner{% endblock %}
{% block page_title %}My Page{% endblock %}

{% block content %}
<div class="page-header">
    <div>
        <h1>My Page</h1>
        <p class="page-subtitle">Description</p>
    </div>
</div>
<!-- page content -->
{% endblock %}
```

2. Add sidebar link in `components/sidebar.html` under appropriate group
3. Add route in `app/routes/` with proper authentication
4. Add tests in `tests/test_ui_shell.py`

## Responsive Behavior

| Breakpoint | Sidebar | Topbar | Content |
|------------|---------|--------|---------|
| ≥768px | Fixed left, collapsible | Sticky top | Margin-left offset |
| <768px | Offcanvas (hamburger) | Full width | Full width |

## HTMX Integration

Pages use HTMX for partial updates without full page reloads. Common patterns:

```html
<!-- Load table on page load -->
<div hx-get="/scanner-results/table" hx-trigger="load" hx-swap="innerHTML"></div>

<!-- Refresh on button click -->
<button hx-post="/scheduler/run-delivery" hx-target="#status">Run</button>

<!-- Search with debounce -->
<input type="text" hx-get="/stocks/search" hx-trigger="input changed delay:300ms" hx-target="#results">
```
