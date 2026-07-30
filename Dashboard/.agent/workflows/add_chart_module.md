---
description: Add a new chart module to the UBC Asset Management Dashboard (end-to-end process).
---

# Add a New Chart Module

Current documentation refresh: 2026-04-28.

## Overview

This workflow guides you through creating a new chart module, registering it in the Flask app, adding a route, and embedding it in the dashboard template.

---

## Step 1: Create the Chart Module

Create a new Python file in `charts/`:

```
Dashboard/charts/my_new_chart.py
```

Follow the standard Matplotlib pattern:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import io
import os

conn = db.get_connection()  # PostgreSQL qr_code_db in production via DB_BACKEND/QR_PG_DSN


def render_chart_png(building: str = "All", **kwargs) -> bytes:
    """Renders the chart and returns PNG bytes."""
    # 1. Load data
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM your_table", conn)

    # 2. Filter
    if building != "All":
        df = df[df["Property"] == building]

    if df.empty:
        return b""

    # 3. Draw chart
    fig, ax = plt.subplots(figsize=(10, 6))
    # ... your chart logic ...
    ax.set_title(f"My Chart â€” {building}")

    # 4. Save to buffer
    buffer = io.BytesIO()
    plt.savefig(buffer, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buffer.seek(0)

    return buffer.getvalue()
```

---

## Step 2: Register the Import in the Main App

Open `Asset_portal_dashboard.py` and add a guarded import in the **Chart Modules Import Section** (around line 58-140):

```python
# N. Try Import: My New Chart
try:
    from charts import my_new_chart as my_new_chart_mod
    MY_NEW_CHART_AVAILABLE = True
except Exception as _e:
    MY_NEW_CHART_AVAILABLE = False
    error_msg = f"My New Chart Error: {str(_e)}"
    if CHARTS_IMPORT_ERROR:
        CHARTS_IMPORT_ERROR += f" | {error_msg}"
    else:
        CHARTS_IMPORT_ERROR = error_msg
```

---

## Step 3: Add the Flask Route

Add a new route in the **Chart Routes** section (around line 1892):

```python
@main_bp.route('/chart/my-new-chart')
@login_required
def my_new_chart():
    if not MY_NEW_CHART_AVAILABLE:
        return Response(b'', mimetype='image/png')
    building = request.args.get('building', 'All')
    data = my_new_chart_mod.render_chart_png(building=building)
    return Response(data, mimetype='image/png')
```

---

## Step 4: Embed in the Dashboard Template

In `templates/dashboard.html`, add an `<img>` tag inside the appropriate view:

```html
<div class="analytics-card">
    <h5 class="section-title">My New Chart</h5>
    <img id="my-new-chart-img"
         src="/chart/my-new-chart?building=All"
         alt="My New Chart"
         style="max-width:100%; height:auto;">
</div>
```

---

## Step 5: Add JavaScript Filter Integration (Optional)

If the chart needs to respond to the building filter:

```javascript
function refreshMyNewChart() {
    const building = document.getElementById('building-filter').value || 'All';
    const img = document.getElementById('my-new-chart-img');
    img.src = `/chart/my-new-chart?building=${encodeURIComponent(building)}&t=${Date.now()}`;
}

// Hook into existing filter change event
document.getElementById('building-filter').addEventListener('change', refreshMyNewChart);
```

---

## Step 6: Test

1. Restart the Flask server
2. Check startup logs for import errors
3. Navigate to the chart route directly: `http://127.0.0.1:8002/chart/my-new-chart`
4. Verify the chart appears in the dashboard view
5. Test with different building filter values
