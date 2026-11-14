# Simulation Scripts — Tester Guide

This `simulation_scripts` folder is **completely standalone** — you do **not** need the rest of the project code on your computer. Just copy or open this folder and run any of the scripts below while connected to the **same Wi-Fi or local network** as the Henvironment server (`192.168.11.50`).

---

## Which Script Should I Run?

| Script | Air Quality Scenario | What It Simulates |
| :--- | :--- | :--- |
| `simulate_good.sh` | **Good / Normal** | Healthy, normal coop conditions |
| `simulate_mid.sh` | **Warning** | Slightly high temperature, gas, or dust levels |
| `simulate_bad.sh` | **Critical** | Unsafe levels that trigger alerts and warnings |
| `simulate_random.sh` | **Random Mix** | Mixed readings across good, warning, and critical levels |
| `delete_simulated_data.sh` | **Clean Up** | Removes all test data created by these scripts (safe for real sensor data) |

---

## Before You Start

1. **Same network:** Connect your computer to the same Wi-Fi or LAN as the Henvironment server (`192.168.11.50`).
2. **Python 3 installed** on your computer.
3. Open a bash terminal inside this `simulation_scripts` folder:
   - **Windows:** Right-click inside the `simulation_scripts` folder → **Open Git Bash here** (or double-click a `.sh` script if Git Bash is your default).
   - **Mac / Linux:** Open **Terminal** and run `cd path/to/simulation_scripts`.

*(First-time setup is automatic: if your computer does not have the database connector yet, the script sets it up quietly in the background on first run.)*

---

## Step 1: Run a Test Scenario

From inside `simulation_scripts`, run whichever scenario you want:

```bash
./simulate_good.sh
```

Swap `simulate_good.sh` for `simulate_mid.sh`, `simulate_bad.sh`, or `simulate_random.sh` to test the other conditions.

*(If the server is ever at a different IP address, pass it at the end — for example: `./simulate_good.sh 192.168.11.50`.)*

### What happens while it runs
- Immediately sends starter sample readings so the dashboard updates right away.
- Continues sending 1 new sample reading every minute while left open.
- Open `http://192.168.11.50:5000` in your browser to inspect the dashboard cards, charts, and 2-hour forecast.
- Press **`Ctrl + C`** whenever you want to stop sending test readings.

---

## Step 2: Clean Up When You're Done

Run the cleanup script afterward so no sample test data stays in the dashboard:

```bash
./delete_simulated_data.sh
```

- Deletes any simulated readings and simulated forecasts found on `192.168.11.50`.
- Safe to run even when there is no test data in the database.
- Never deletes real hardware sensor readings.

---

## Need Help?

- **Script says it cannot reach `192.168.11.50:5432`:**
  - Make sure you are on the same Wi-Fi/LAN as the server.
  - Ask the project team to confirm the server is on and port `5432` is reachable.
- **"Permission denied" on Mac or Linux:**
  - Run `chmod +x *.sh` inside `simulation_scripts` once, then try again.
- **"Python 3 is required" message:**
  - Install Python 3 from [python.org](https://www.python.org/downloads/), open a fresh terminal window, and run the script again.
