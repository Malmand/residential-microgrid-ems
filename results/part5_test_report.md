# Closed-loop test report: battery site controller

2 site configurations x 8 scenarios x 5 controller versions, 180 checks.

## Summary

| Controller | Checks | Passed | Failed |
|---|---:|---:|---:|
| Controller v1 | 36 | 33 | 3 |
| Controller v2 | 36 | 36 | 0 |
| v2 + fault: FCR sign error | 36 | 14 | 22 |
| v2 + fault: filtered FCR (60 s) | 36 | 14 | 22 |
| v2 + fault: connection limit ignored | 36 | 34 | 2 |

## Failed checks

| Site | Scenario | Controller | Check | Value | Limit |
|---|---|---|---|---:|---|
| 15 MW / 30 MWh (2 h) | Normal frequency (noise) | v2 + fault: FCR sign error | FCR tracking error (RMS) | 20.477 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Normal frequency (noise) | v2 + fault: FCR sign error | FCR response direction (correlation) | -0.984 - | >= 0.9 |
| 15 MW / 30 MWh (2 h) | Normal frequency (noise) | v2 + fault: filtered FCR (60 s) | FCR tracking error (RMS) | 8.576 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Normal frequency (noise) | v2 + fault: filtered FCR (60 s) | FCR response direction (correlation) | 0.552 - | >= 0.9 |
| 15 MW / 30 MWh (2 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 30 MWh (2 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: FCR sign error | FCR sustained within 2% (15 min) | 0.0 % of time | >= 99 |
| 15 MW / 30 MWh (2 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR full activation time | 234.6 s | <= 30 |
| 15 MW / 30 MWh (2 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR sustained within 2% (15 min) | 77.516 % of time | >= 99 |
| 15 MW / 30 MWh (2 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 30 MWh (2 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: FCR sign error | FCR sustained within 2% (15 min) | 0.0 % of time | >= 99 |
| 15 MW / 30 MWh (2 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR full activation time | 234.6 s | <= 30 |
| 15 MW / 30 MWh (2 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR sustained within 2% (15 min) | 77.516 % of time | >= 99 |
| 15 MW / 30 MWh (2 h) | Frequency ramp to 49.85 Hz | v2 + fault: FCR sign error | FCR tracking error (RMS) | 122.378 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Frequency ramp to 49.85 Hz | v2 + fault: FCR sign error | FCR response direction (correlation) | -1.0 - | >= 0.9 |
| 15 MW / 30 MWh (2 h) | Frequency ramp to 49.85 Hz | v2 + fault: filtered FCR (60 s) | FCR tracking error (RMS) | 20.454 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Frequency ramp to 49.85 Hz | v2 + fault: filtered FCR (60 s) | FCR response direction (correlation) | 0.891 - | >= 0.9 |
| 15 MW / 30 MWh (2 h) | Under-frequency step at low SOC | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 30 MWh (2 h) | Under-frequency step at low SOC | v2 + fault: filtered FCR (60 s) | FCR full activation time | 234.6 s | <= 30 |
| 15 MW / 30 MWh (2 h) | Wind surge at the connection limit | Controller v1 | FCR tracking error (RMS) | 9.033 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Wind surge at the connection limit | v2 + fault: FCR sign error | FCR tracking error (RMS) | 21.362 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Wind surge at the connection limit | v2 + fault: filtered FCR (60 s) | FCR tracking error (RMS) | 8.768 % of FCR | <= 5 |
| 15 MW / 30 MWh (2 h) | Wind surge at the connection limit | v2 + fault: connection limit ignored | Time above connection limit | 299.6 s | <= 1 |
| 15 MW / 30 MWh (2 h) | Step with 1 s communication delay | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 30 MWh (2 h) | Step with 1 s communication delay | v2 + fault: filtered FCR (60 s) | FCR full activation time | 235.4 s | <= 30 |
| 15 MW / 15 MWh (1 h) | Normal frequency (noise) | v2 + fault: FCR sign error | FCR tracking error (RMS) | 20.477 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Normal frequency (noise) | v2 + fault: FCR sign error | FCR response direction (correlation) | -0.984 - | >= 0.9 |
| 15 MW / 15 MWh (1 h) | Normal frequency (noise) | v2 + fault: filtered FCR (60 s) | FCR tracking error (RMS) | 8.576 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Normal frequency (noise) | v2 + fault: filtered FCR (60 s) | FCR response direction (correlation) | 0.552 - | >= 0.9 |
| 15 MW / 15 MWh (1 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 15 MWh (1 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: FCR sign error | FCR sustained within 2% (15 min) | 0.0 % of time | >= 99 |
| 15 MW / 15 MWh (1 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR full activation time | 234.6 s | <= 30 |
| 15 MW / 15 MWh (1 h) | Under-frequency step -200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR sustained within 2% (15 min) | 77.516 % of time | >= 99 |
| 15 MW / 15 MWh (1 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 15 MWh (1 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: FCR sign error | FCR sustained within 2% (15 min) | 0.0 % of time | >= 99 |
| 15 MW / 15 MWh (1 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR full activation time | 234.6 s | <= 30 |
| 15 MW / 15 MWh (1 h) | Over-frequency step +200 mHz, 15 min | v2 + fault: filtered FCR (60 s) | FCR sustained within 2% (15 min) | 77.516 % of time | >= 99 |
| 15 MW / 15 MWh (1 h) | Frequency ramp to 49.85 Hz | v2 + fault: FCR sign error | FCR tracking error (RMS) | 122.378 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Frequency ramp to 49.85 Hz | v2 + fault: FCR sign error | FCR response direction (correlation) | -1.0 - | >= 0.9 |
| 15 MW / 15 MWh (1 h) | Frequency ramp to 49.85 Hz | v2 + fault: filtered FCR (60 s) | FCR tracking error (RMS) | 20.454 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Frequency ramp to 49.85 Hz | v2 + fault: filtered FCR (60 s) | FCR response direction (correlation) | 0.891 - | >= 0.9 |
| 15 MW / 15 MWh (1 h) | Under-frequency step at low SOC | Controller v1 | Lowest SOC | 4.647 % | >= 5 |
| 15 MW / 15 MWh (1 h) | Under-frequency step at low SOC | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 15 MWh (1 h) | Under-frequency step at low SOC | v2 + fault: filtered FCR (60 s) | FCR full activation time | 234.6 s | <= 30 |
| 15 MW / 15 MWh (1 h) | Wind surge at the connection limit | Controller v1 | FCR tracking error (RMS) | 9.033 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Wind surge at the connection limit | v2 + fault: FCR sign error | FCR tracking error (RMS) | 21.362 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Wind surge at the connection limit | v2 + fault: filtered FCR (60 s) | FCR tracking error (RMS) | 8.768 % of FCR | <= 5 |
| 15 MW / 15 MWh (1 h) | Wind surge at the connection limit | v2 + fault: connection limit ignored | Time above connection limit | 299.6 s | <= 1 |
| 15 MW / 15 MWh (1 h) | Step with 1 s communication delay | v2 + fault: FCR sign error | FCR full activation time | inf s | <= 30 |
| 15 MW / 15 MWh (1 h) | Step with 1 s communication delay | v2 + fault: filtered FCR (60 s) | FCR full activation time | 235.4 s | <= 30 |
