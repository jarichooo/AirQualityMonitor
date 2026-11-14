# Testing Guide (`simulation_scripts`)

This folder contains ready-to-use test scripts for **people testing the Henvironment Air Quality Dashboard** without needing physical sensors or server login access.

The scripts are pre-configured for the Henvironment server at **`192.168.11.50`**. As long as your laptop or computer is on the **same Wi-Fi or local network**, you can run them right away from your own device.

---

## Which Script Should I Run?

Pick the test scenario you want to see on the dashboard:

| Script | Air Quality Scenario | What It Simulates |
| :--- | :--- | :--- |
| `simulate_good.sh` | **Good / Normal** | Healthy, normal coop conditions |
| `simulate_mid.sh` | **Warning** | Slightly high temperature, gas, or dust levels |
| `simulate_bad.sh` | **Critical** | Unsafe levels that trigger alerts and warnings |
| `simulate_random.sh` | **Random Mix** | Mixed readings across good, warning, and critical levels |
| `delete_simulated_data.sh` | **Clean Up** | Removes all test data created by these scripts (safe for real sensor data) |

---

## What You Need Before Starting

1. **Same network:** Connect your computer to the same Wi-Fi or LAN as the Henvironment server (`192.168.11.50`).
2. **Python 3 installed** on your computer.
3. A bash terminal:
   - **Windows:** Open **Git Bash** inside the `AirQualityMonitor` folder (right-click the folder → **Open Git Bash here**).
   - **Mac / Linux:** Open **Terminal** and go into the `AirQualityMonitor` folder (`cd path/to/AirQualityMonitor`).

*(The first time you run a script, it may pause for a few seconds to install a small helper package automatically — you don't need to install anything manually.)*

---

## Step 1: Run a Test Scenario

Simply run the script you want:

```bash
./simulation_scripts/simulate_good.sh
```

Swap `simulate_good.sh` for `simulate_mid.sh`, `simulate_bad.sh`, or `simulate_random.sh` to test the other conditions.

*(If the server ever moves to a different IP address, just pass the new IP at the end — for example: `./simulation_scripts/simulate_good.sh 192.168.11.50`.)*

### What happens while it runs
- Immediately sends starter sample readings so the dashboard cards and charts update right away.
- Continues sending 1 new sample reading every minute while open.
- Open the **Henvironment web dashboard** (`http://192.168.11.50:5000`) in your browser to inspect the cards, charts, and 2-hour forecast.
- Press **`Ctrl + C`** in the terminal whenever you want to stop the test.

---

## Step 2: Clean Up When You're Done

After finishing your tests, run the cleanup script so no sample test data stays mixed into the system:

```bash
./simulation_scripts/delete_simulated_data.sh
```

- Deletes any simulated readings and simulated forecasts found on the server (`192.168.11.50`).
- Safe to run even if there is no test data yet.
- Does **not** delete real sensor readings from actual hardware.

---

## If Something Doesn't Work

- **Script says it cannot reach `192.168.11.50:5432`:**
  - Make sure your device is on the same Wi-Fi/network as the server.
  - Check with the project team if the server is powered on or if port `5432` is blocked on the server.
- **"Permission denied" on Mac or Linux:**
  - Run `chmod +x simulation_scripts/*.sh` once, then re-run your script.
- **"Python 3 is required" message:**
  - Install Python 3 from [python.org](https://www.python.org/downloads/), then open a fresh terminal window and try again.
