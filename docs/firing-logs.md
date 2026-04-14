# Firing Logs

Every firing is automatically logged with temperature history, cost, duration, and divergence metrics. Logs are viewable from the dashboard's Past Firings gallery and accessible via the REST API.

## What Gets Logged

A firing log is saved when a firing completes, is aborted, or triggers an emergency stop.

Each log contains:

| Field | Description |
|-------|-------------|
| `profile_name` | Name of the firing profile |
| `start_time` | ISO 8601 timestamp |
| `end_time` | ISO 8601 timestamp |
| `duration_seconds` | Total firing duration |
| `status` | `"completed"`, `"aborted"`, or `"emergency_stop"` |
| `final_cost` | Calculated electricity cost |
| `final_temperature` | Kiln temperature at end of firing |
| `avg_divergence` | Average temperature divergence from target (see below) |
| `temp_scale` | Temperature unit (`"f"` or `"c"`) |
| `currency_type` | Currency symbol (e.g., `"$"`) |
| `temperature_log` | Array of up to 500 sampled data points |
| `adjusted_profile` | Target curve adjusted to actual start temperature |

### Temperature Log Entries

Each entry in `temperature_log`:

```json
{
  "runtime": 1234,
  "actual_elapsed_time": 1234.5,
  "temperature": 1250.3,
  "target": 1260.0
}
```

The raw data is downsampled to a maximum of 500 points for reasonable file sizes while maintaining adequate resolution for historical graphs.

## Divergence Tracking

Divergence measures how closely the kiln followed the target profile throughout a firing.

- **Per sample**: `abs(target_temperature - actual_temperature)`
- **Multi-zone**: Uses the maximum divergence across all zones at each sample
- **Average**: Sum of all samples divided by count, displayed as `+-X.X degrees`

Low divergence (e.g., +-0.2 degrees) indicates tight temperature control. High divergence (e.g., +-49 degrees) suggests the kiln struggled to follow the profile — possibly due to PID tuning, element capacity, or ambient conditions.

## Storage

Firing logs are saved to `storage/firing_logs/` as JSON files.

**Filename format**: `YYYY-MM-DD_HH-MM-SS_<profile-name>.json`

Example: `2026-04-14_09-32-27_cone-05-long-bisque.json`

## Past Firings Gallery

The dashboard displays a horizontal scrollable gallery of recent firings below the main graph.

Each card shows:
- Profile name
- Date
- Duration (HH:MM:SS)
- Cost
- Divergence (degrees)
- Status icon (checkmark for completed, circle-slash for aborted, warning for emergency)

**Interactions:**
- **Click a card** to view the historical firing curve overlaid on the target profile
- **Click the pin icon** to pin a firing to the top of the gallery
- **Delete** with two-click confirmation (click delete, then confirm)
- **Load More** card appears when additional logs exist beyond the current page

The gallery is disabled during active firings to prevent accidental navigation.

## Pinned Logs

Pin important firings for quick reference. Pinned logs appear at the top of the gallery in pin order, followed by unpinned logs sorted newest-first.

Pinned filenames are stored in `storage/pinned_logs.json`.

## API Endpoints

### List Firing Logs

```
GET /api/firing_logs?limit=7&offset=0
```

Returns summary data (no temperature_log) sorted newest-first:

```json
{
  "logs": [
    {
      "filename": "2026-04-14_09-32-27_cone-05-long-bisque.json",
      "profile_name": "cone-05-long-bisque",
      "end_time": "2026-04-14T09:32:27.810113",
      "duration_seconds": 2924,
      "final_cost": 0.02,
      "avg_divergence": 0.98,
      "currency_type": "$",
      "status": "aborted"
    }
  ],
  "hasMore": true
}
```

### Get Full Firing Log

```
GET /api/firing_log/<filename>
```

Returns the complete log including `temperature_log` and `adjusted_profile` for graph rendering.

### Delete Firing Log

```
DELETE /api/firing_log/<filename>
```

Deletes the file and automatically removes it from pinned logs if pinned.

### List Pinned Logs

```
GET /api/pinned_logs
```

```json
{
  "pinned": ["2026-04-14_09-32-27_cone-05-long-bisque.json"]
}
```

### Pin / Unpin

```
POST /api/pinned_logs/<filename>    # Pin
DELETE /api/pinned_logs/<filename>   # Unpin
```

## Historical Graph View

Clicking a gallery card fetches the full log and displays two overlaid series:

- **Target curve** (green, dashed): The adjusted profile curve
- **Actual temperature** (blue, filled): Recorded kiln temperature

Both curves are time-aligned starting from zero. For aborted firings, the target curve is clipped to the actual firing duration.

Click the close button or anywhere outside the overlay to return to the live view.

## Cost Calculation

```
cost = (average_heat_duty * kw_elements * duration_hours) * kwh_rate
```

Configure in `config.py`:
- `kw_elements`: Total kiln wattage in kilowatts
- `kwh_rate`: Electricity cost per kWh
- `currency_type`: Display currency symbol
