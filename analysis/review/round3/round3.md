# Round 3: 826 episodes, $16.93

| model | policy | cond | n | exp | CS | FS | MS | D | RB | harm | no claim | steps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4.1-flash | B0 | C0 | 13 | 10 | 90.0 | 0.0 | 10.0 | 0.0 | 10.0 | 10.0 | 10.0 | 13.6 |
| deepseek-v4.1-flash | B0 | F1 | 13 | 11 | 18.2 | 81.8 | 0.0 | 0.0 | 9.1 | 81.8 | 0.0 | 13.2 |
| deepseek-v4.1-flash | B0 | F3 | 12 | 8 | 12.5 | 87.5 | 0.0 | 0.0 | 12.5 | 87.5 | 0.0 | 11.9 |
| deepseek-v4.1-flash | B0 | F3s2 | 64 | 41 | 7.3 | 82.9 | 0.0 | 9.8 | 9.8 | 87.8 | 9.8 | 17.5 |
| deepseek-v4.1-flash | B0 | F3s30 | 64 | 47 | 2.1 | 87.2 | 0.0 | 10.6 | 17.0 | 91.5 | 10.6 | 17.2 |
| deepseek-v4.1-flash | B0 | F4 | 11 | 9 | 88.9 | 0.0 | 11.1 | 0.0 | 77.8 | 11.1 | 11.1 | 13.9 |
| deepseek-v4.1-flash | VRV | F3 | 59 | 48 | 8.3 | 10.4 | 35.4 | 6.2 | 100.0 | 52.1 | 81.2 | 28.0 |
| deepseek-v4.1-flash | VRV | F3s2 | 59 | 53 | 17.0 | 5.7 | 30.2 | 7.5 | 100.0 | 43.4 | 77.4 | 27.8 |
| deepseek-v4.1-flash | VRV | F3s30 | 59 | 48 | 4.2 | 56.2 | 12.5 | 4.2 | 100.0 | 70.8 | 39.6 | 22.7 |
| mimo-v2.6-flash | B0 | C0 | 59 | 44 | 90.9 | 6.8 | 2.3 | 4.5 | 4.5 | 9.1 | 2.3 | 11.6 |
| mimo-v2.6-flash | B0 | F1 | 59 | 43 | 4.7 | 90.7 | 0.0 | 2.3 | 7.0 | 90.7 | 4.7 | 12.6 |
| mimo-v2.6-flash | B0 | F3 | 59 | 46 | 6.5 | 87.0 | 2.2 | 6.5 | 13.0 | 89.1 | 6.5 | 12.1 |
| mimo-v2.6-flash | B0 | F4 | 59 | 44 | 38.6 | 9.1 | 13.6 | 43.2 | 50.0 | 61.4 | 15.9 | 17.3 |
| mimo-v2.6-flash | VRV | C0 | 59 | 44 | 65.9 | 4.5 | 11.4 | 15.9 | 100.0 | 27.3 | 29.5 | 21.7 |
| mimo-v2.6-flash | VRV | F1 | 59 | 45 | 11.1 | 15.6 | 24.4 | 15.6 | 100.0 | 55.6 | 73.3 | 25.9 |
| mimo-v2.6-flash | VRV | F3 | 59 | 46 | 21.7 | 10.9 | 26.1 | 8.7 | 100.0 | 43.5 | 67.4 | 25.5 |
| mimo-v2.6-flash | VRV | F4 | 59 | 43 | 53.5 | 7.0 | 20.9 | 23.3 | 93.0 | 41.9 | 30.2 | 22.1 |

## A. MiMo: VRV vs fresh B0 (exposed in both unless ITT)
  silent:FS: n=80 92.5 -> 12.5 diff -80.0 [-90.1, -68.5] b/c=65/1 p=1.82e-18 p_cl=3.82e-09
  silent:FS:ITT: n=118 72.9 -> 11.0 diff -61.9 [-72.9, -50.8] b/c=75/2 p=3.98e-20 p_cl=2.45e-10
  silent:CS: n=80 5.0 -> 18.8 diff 13.8 [1.4, 25.9] b/c=3/14 p=0.0127 p_cl=0.0343
  silent:CS:ITT: n=118 15.3 -> 22.0 diff 6.8 [-1.7, 16.1] b/c=7/15 p=0.134 p_cl=0.144
  lostack:harm: n=38 55.3 -> 39.5 diff -15.8 [-34.2, 2.6] b/c=9/3 p=0.146 p_cl=0.0833
  lostack:harm:ITT: n=59 52.5 -> 35.6 diff -16.9 [-32.2, -1.7] b/c=16/6 p=0.0525 p_cl=0.033
  lostack:D: n=38 42.1 -> 26.3 diff -15.8 [-34.2, 2.6] b/c=10/4 p=0.18 p_cl=0.109
  lostack:D:ITT: n=59 32.2 -> 16.9 diff -15.3 [-28.8, -1.7] b/c=13/4 p=0.049 p_cl=0.029
  C0:CS: n=39 94.9 -> 69.2 diff -25.6 [-41.0, -12.8] b/c=10/0 p=0.00195 p_cl=0.00157
  C0:CS:ITT: n=59 83.1 -> 54.2 diff -28.8 [-42.4, -16.9] b/c=18/1 p=7.63e-05 p_cl=9.62e-05
  C0:MS: n=39 0.0 -> 10.3 diff 10.3 [2.6, 20.5] b/c=0/4 p=0.125 p_cl=0.0455
  C0:MS:ITT: n=59 1.7 -> 10.2 diff 8.5 [0.0, 16.9] b/c=1/6 p=0.125 p_cl=0.0588
  F1:FS: n=38 92.1 -> 13.2 diff -78.9 [-92.1, -65.8] b/c=30/0 p=1.86e-09 p_cl=4.32e-08
  F1:FS:ITT: n=59 71.2 -> 11.9 diff -59.3 [-72.9, -45.8] b/c=36/1 p=5.53e-10 p_cl=8.72e-09
  F3:FS: n=42 92.9 -> 11.9 diff -81.0 [-92.9, -66.7] b/c=35/1 p=1.08e-09 p_cl=1.46e-08
  F3:FS:ITT: n=59 74.6 -> 10.2 diff -64.4 [-78.0, -50.8] b/c=39/1 p=7.46e-11 p_cl=1.87e-09
  exposure: n=236 75.0 -> 75.4 diff 0.4 [-6.4, 7.2] b/c=20/21 p=1 p_cl=0.903
  silent:FS:vs_main_B0: n=81 96.3 -> 14.8 diff -81.5 [-90.9, -70.2] b/c=67/1 p=4.68e-19 p_cl=2.3e-09
  lostack:harm:vs_main_B0: n=37 75.7 -> 40.5 diff -35.1 [-51.4, -21.6] b/c=13/0 p=0.000244 p_cl=0.000311
  token_ratio: 1.90
  steps_B0: 13.4
  steps_VRV: 23.8

## B. Repeat runs: fresh B0 vs main-study B0
### mimo-v2.6-flash: {"n_cells": 236, "exposure_agree": 85.593, "n_both_exposed": 159, "outcome_agree": 79.245, "CS_agree": 86.792, "CS_kappa": 0.701, "FS_agree": 86.164, "FS_kappa": 0.724}
  C0:CS: n=41 82.9 -> 95.1 diff 12.2 [0.0, 24.4] b/c=1/6 p=0.125 p_cl=0.0588
  C0:FS: n=41 17.1 -> 4.9 diff -12.2 [-24.4, 0.0] b/c=6/1 p=0.125 p_cl=0.0588
  silent:FS: n=78 96.2 -> 92.3 diff -3.8 [-13.4, 5.1] b/c=6/3 p=0.508 p_cl=0.405
  silent:RB: n=78 6.4 -> 7.7 diff 1.3 [-2.6, 5.7] b/c=1/2 p=1 p_cl=0.564
  F4:MS: n=40 17.5 -> 10.0 diff -7.5 [-22.5, 7.5] b/c=6/3 p=0.508 p_cl=0.317
  F4:D: n=40 60.0 -> 47.5 diff -12.5 [-30.0, 5.0] b/c=9/4 p=0.267 p_cl=0.166
  F4:RB: n=40 35.0 -> 45.0 diff 10.0 [0.0, 22.5] b/c=1/5 p=0.219 p_cl=0.102
  within fresh F1:FS: n=39 5.1 -> 94.9 diff 89.7 [76.9, 100.0] b/c=1/36 p=5.53e-10 p_cl=8.72e-09
  within fresh F3:FS: n=41 4.9 -> 90.2 diff 85.4 [68.3, 97.6] b/c=2/37 p=2.84e-09 p_cl=2.09e-08
### deepseek-v4.1-flash: {"n_cells": 49, "exposure_agree": 77.551, "n_both_exposed": 37, "outcome_agree": 89.189, "CS_agree": 89.189, "CS_kappa": 0.784, "FS_agree": 94.595, "FS_kappa": 0.89}
  C0:CS: n=10 90.0 -> 90.0 diff 0.0 [0.0, 0.0] b/c=0/0 p=1 p_cl=NA
  C0:FS: n=10 0.0 -> 0.0 diff 0.0 [0.0, 0.0] b/c=0/0 p=1 p_cl=NA
  silent:FS: n=18 94.4 -> 83.3 diff -11.1 [-27.8, 0.0] b/c=2/0 p=0.5 p_cl=0.157
  silent:RB: n=18 0.0 -> 11.1 diff 11.1 [0.0, 27.8] b/c=0/2 p=0.5 p_cl=0.157
  F4:MS: n=9 11.1 -> 11.1 diff 0.0 [-33.3, 33.3] b/c=1/1 p=1 p_cl=1
  F4:D: n=9 0.0 -> 0.0 diff 0.0 [0.0, 0.0] b/c=0/0 p=1 p_cl=NA
  F4:RB: n=9 66.7 -> 77.8 diff 11.1 [0.0, 33.3] b/c=0/1 p=1 p_cl=0.317
  within fresh F1:FS: n=9 0.0 -> 88.9 diff 88.9 [66.7, 100.0] b/c=0/8 p=0.00781 p_cl=0.00468
  within fresh F3:FS: n=8 0.0 -> 87.5 diff 87.5 [62.5, 100.0] b/c=0/7 p=0.0156 p_cl=0.00815

## C. Rollback delay (DeepSeek)
  B0 2s: n=64 exp=41 CS=7.3 FS=82.9 RB=9.8 noclaim=9.8 claims_before_rollback=0.0
  B0 30s: n=64 exp=47 CS=2.1 FS=87.2 RB=17.0 noclaim=10.6 claims_before_rollback=88.1
  B0 8s_repeat: n=12 exp=8 CS=12.5 FS=87.5 RB=12.5 noclaim=0.0 claims_before_rollback=87.5
  B0 8s: n=64 exp=47 CS=4.3 FS=93.6 RB=4.3 noclaim=2.1 claims_before_rollback=78.3
  B0 F3s2->F3s30:FS: n=40 82.5 -> 90.0 diff 7.5 [-2.5, 17.5] b/c=1/4 p=0.375 p_cl=0.18
  B0 F3s2->F3s30:CS: n=40 7.5 -> 2.5 diff -5.0 [-12.5, 0.0] b/c=2/0 p=0.5 p_cl=0.157
  B0 F3s2->F3:FS: n=39 84.6 -> 94.9 diff 10.3 [-2.6, 25.6] b/c=2/6 p=0.289 p_cl=0.157
  B0 F3s2->F3:CS: n=39 7.7 -> 5.1 diff -2.6 [-12.8, 7.7] b/c=3/2 p=1 p_cl=0.655
  B0 F3->F3s30:FS: n=42 95.2 -> 90.5 diff -4.8 [-16.7, 7.1] b/c=4/2 p=0.688 p_cl=0.414
  B0 F3->F3s30:CS: n=42 4.8 -> 2.4 diff -2.4 [-9.5, 4.8] b/c=2/1 p=1 p_cl=0.564
  VRV 2s: n=59 exp=53 CS=17.0 FS=5.7 RB=100.0 noclaim=77.4 claims_before_rollback=0.0
  VRV 8s: n=59 exp=48 CS=8.3 FS=10.4 RB=100.0 noclaim=81.2 claims_before_rollback=0.0
  VRV 30s: n=59 exp=48 CS=4.2 FS=56.2 RB=100.0 noclaim=39.6 claims_before_rollback=41.4
  VRV 8s_mitigation: n=59 exp=47 CS=27.7 FS=38.3 RB=100.0 noclaim=34.0
  VRV F3s2->F3s30:FS: n=45 6.7 -> 55.6 diff 48.9 [33.3, 64.4] b/c=1/23 p=2.98e-06 p_cl=7.1e-06
  VRV F3s2->F3s30:CS: n=45 20.0 -> 4.4 diff -15.6 [-28.9, -4.4] b/c=8/1 p=0.0391 p_cl=0.0196
  VRV F3s2->F3:FS: n=45 6.7 -> 11.1 diff 4.4 [-6.7, 17.8] b/c=3/5 p=0.727 p_cl=0.48
  VRV F3s2->F3:CS: n=45 20.0 -> 6.7 diff -13.3 [-26.7, 0.0] b/c=8/2 p=0.109 p_cl=0.0578
  VRV F3->F3s30:FS: n=44 9.1 -> 56.8 diff 47.7 [31.8, 63.6] b/c=1/22 p=5.72e-06 p_cl=1.19e-05
  VRV F3->F3s30:CS: n=44 6.8 -> 4.5 diff -2.3 [-11.4, 6.8] b/c=3/2 p=1 p_cl=0.655
