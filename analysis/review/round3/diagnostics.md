# Round-3 diagnostics

Inputs opened read-only; no network, Docker, queue mutation, or credential files. Released episode lists define the population; directories are runs/<episode>, exactly as export_r2.validate and analyze.capture resolve them. No replacement attempts are selected.

### Missing directories across entire input lists
| list | episodes | missing run directories |
| --- | --- | --- |
| r3 | 826 | 0 |
| mitigation | 2832 | 0 |
| main | 1631 | 0 |

## 1. Parse errors and composition
E1/E2/E3 are, respectively:
- E1: Reply with exactly one action per turn.
- E2: Use exactly one ```action fenced block containing one valid action call.
- E3: Elements must be referenced by the bracketed numeric bid shown in the observation.

Count exactly action == "noop()" and error in the above set. Steps are trace entries, not API calls. Means divide by episodes with available traces; missing traces are excluded, never treated as zero. Step shares divide by observed trace steps. All episodes, including unexposed episodes, enter these groups.
| list | model | policy | condition | episodes | missing dirs | missing traces | steps | parse errors | share | mean/episode | E1 | E2 | E3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mitigation | deepseek-v4.1-flash | VRV | C0 | 59 | 0 | 0 | 1048 | 85 | 8.110687% | 1.440678 | 16 | 66 | 3 |
| mitigation | deepseek-v4.1-flash | VRV | F1 | 59 | 0 | 0 | 1471 | 122 | 8.293678% | 2.067797 | 45 | 73 | 4 |
| mitigation | deepseek-v4.1-flash | VRV | F3 | 59 | 0 | 0 | 1311 | 83 | 6.331045% | 1.406780 | 19 | 58 | 6 |
| main | deepseek-v4.1-flash | B0 | C0 | 64 | 0 | 0 | 800 | 31 | 3.875000% | 0.484375 | 17 | 13 | 1 |
| main | deepseek-v4.1-flash | B0 | F3 | 64 | 0 | 0 | 818 | 39 | 4.767726% | 0.609375 | 10 | 27 | 2 |
| r3 | deepseek-v4.1-flash | VRV | F3s2 | 59 | 0 | 0 | 1638 | 355 | 21.672772% | 6.016949 | 10 | 342 | 3 |
| r3 | deepseek-v4.1-flash | VRV | F3 | 59 | 0 | 0 | 1652 | 375 | 22.699758% | 6.355932 | 8 | 364 | 3 |
| r3 | deepseek-v4.1-flash | VRV | F3s30 | 59 | 0 | 0 | 1337 | 277 | 20.718025% | 4.694915 | 1 | 274 | 2 |
| r3 | deepseek-v4.1-flash | B0 | C0 | 13 | 0 | 0 | 177 | 58 | 32.768362% | 4.461538 | 0 | 58 | 0 |
| r3 | deepseek-v4.1-flash | B0 | F1 | 13 | 0 | 0 | 172 | 35 | 20.348837% | 2.692308 | 0 | 35 | 0 |
| r3 | deepseek-v4.1-flash | B0 | F3 | 12 | 0 | 0 | 143 | 58 | 40.559441% | 4.833333 | 1 | 57 | 0 |
| r3 | deepseek-v4.1-flash | B0 | F4 | 11 | 0 | 0 | 153 | 36 | 23.529412% | 3.272727 | 2 | 34 | 0 |
| r3 | deepseek-v4.1-flash | B0 | F3s2 | 64 | 0 | 0 | 1120 | 182 | 16.250000% | 2.843750 | 9 | 170 | 3 |
| r3 | deepseek-v4.1-flash | B0 | F3s30 | 64 | 0 | 0 | 1104 | 162 | 14.673913% | 2.531250 | 6 | 147 | 9 |
| r3 | mimo-v2.6-flash | B0 | C0 | 59 | 0 | 0 | 684 | 44 | 6.432749% | 0.745763 | 11 | 31 | 2 |
| r3 | mimo-v2.6-flash | B0 | F1 | 59 | 0 | 0 | 741 | 61 | 8.232119% | 1.033898 | 6 | 52 | 3 |
| r3 | mimo-v2.6-flash | B0 | F3 | 59 | 0 | 0 | 714 | 47 | 6.582633% | 0.796610 | 4 | 37 | 6 |
| r3 | mimo-v2.6-flash | B0 | F4 | 59 | 0 | 0 | 1020 | 95 | 9.313725% | 1.610169 | 9 | 78 | 8 |
| r3 | mimo-v2.6-flash | VRV | C0 | 59 | 0 | 0 | 1281 | 150 | 11.709602% | 2.542373 | 22 | 127 | 1 |
| r3 | mimo-v2.6-flash | VRV | F1 | 59 | 0 | 0 | 1528 | 90 | 5.890052% | 1.525424 | 21 | 69 | 0 |
| r3 | mimo-v2.6-flash | VRV | F3 | 59 | 0 | 0 | 1506 | 90 | 5.976096% | 1.525424 | 19 | 67 | 4 |
| r3 | mimo-v2.6-flash | VRV | F4 | 59 | 0 | 0 | 1306 | 89 | 6.814701% | 1.508475 | 24 | 63 | 2 |
| main | mimo-v2.6-flash | B0 | C0 | 64 | 0 | 0 | 984 | 127 | 12.906504% | 1.984375 | 4 | 117 | 6 |
| main | mimo-v2.6-flash | B0 | F1 | 64 | 0 | 0 | 973 | 60 | 6.166495% | 0.937500 | 2 | 57 | 1 |
| main | mimo-v2.6-flash | B0 | F3 | 64 | 0 | 0 | 926 | 62 | 6.695464% | 0.968750 | 4 | 56 | 2 |
| main | mimo-v2.6-flash | B0 | F4 | 64 | 0 | 0 | 1335 | 137 | 10.262172% | 2.140625 | 4 | 130 | 3 |

### Parse-error composition (DeepSeek-V4.1-Flash)
Categories use first-match order: empty/whitespace, literal DSML, at least two opening action fences, remaining fenced replies, prose without a fence, other. No separate response content or reasoning text is stored; raw_model_text is normalized content. The observable proxy uses action_llm_call.usage.reasoning, not aggregate usage.
| Group | Parse errors | Empty/whitespace | DSML | ≥2 action blocks | Fence; no valid single action | No fence (prose) | Other | Empty content + nonempty reasoning | Proxy: empty raw + reasoning tokens >0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| main B0 (C0, F1, F2, F3, F4, F5, F6) | 228 | 98 | 10 | 92 | 21 | 7 | 0 | N/A: fields not stored | 98/228 (43.0%) |
| mitigation VRV (C0, F1, F3, F4, F5, F6) | 527 | 268 | 21 | 174 | 56 | 8 | 0 | N/A: fields not stored | 267/527 (50.7%) |
| r3 B0 repeat (C0, F1, F3, F4) | 187 | 175 | 8 | 3 | 0 | 1 | 0 | N/A: fields not stored | 1/187 (0.5%) |
| r3 B0 delay (F3s2, F3s30) | 344 | 203 | 107 | 15 | 17 | 2 | 0 | N/A: fields not stored | 11/344 (3.2%) |
| r3 VRV (F3, F3s2, F3s30) | 1007 | 224 | 751 | 19 | 12 | 1 | 0 | N/A: fields not stored | 38/1007 (3.8%) |

## 2. DSML share
Markup tests are case-sensitive literal substrings of raw_model_text. Other markup means <tool_call, <function_calls or <invoke; multiple blocks means at least two opening action fences. These categories can overlap. Absent/null replies count as empty; all trace entries enter the share denominator.
| list | model | policy | condition | steps | parse errors | DSML among parse errors | other markup among parse errors | multiple blocks among parse errors | share of all steps with DSML |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| mitigation | deepseek-v4.1-flash | VRV | C0 | 1048 | 85 | 5 | 0 | 16 | 0.572519% |
| mitigation | deepseek-v4.1-flash | VRV | F1 | 1471 | 122 | 4 | 0 | 45 | 0.271924% |
| mitigation | deepseek-v4.1-flash | VRV | F3 | 1311 | 83 | 4 | 0 | 19 | 0.305111% |
| main | deepseek-v4.1-flash | B0 | C0 | 800 | 31 | 1 | 0 | 17 | 0.125000% |
| main | deepseek-v4.1-flash | B0 | F3 | 818 | 39 | 1 | 0 | 10 | 0.122249% |
| r3 | deepseek-v4.1-flash | VRV | F3s2 | 1638 | 355 | 301 | 0 | 10 | 18.376068% |
| r3 | deepseek-v4.1-flash | VRV | F3 | 1652 | 375 | 257 | 0 | 8 | 15.556901% |
| r3 | deepseek-v4.1-flash | VRV | F3s30 | 1337 | 277 | 193 | 0 | 1 | 14.435303% |
| r3 | deepseek-v4.1-flash | B0 | C0 | 177 | 58 | 3 | 0 | 0 | 1.694915% |
| r3 | deepseek-v4.1-flash | B0 | F1 | 172 | 35 | 1 | 0 | 0 | 0.581395% |
| r3 | deepseek-v4.1-flash | B0 | F3 | 143 | 58 | 0 | 0 | 1 | 0.000000% |
| r3 | deepseek-v4.1-flash | B0 | F4 | 153 | 36 | 4 | 0 | 2 | 2.614379% |
| r3 | deepseek-v4.1-flash | B0 | F3s2 | 1120 | 182 | 53 | 0 | 9 | 4.821429% |
| r3 | deepseek-v4.1-flash | B0 | F3s30 | 1104 | 162 | 54 | 0 | 6 | 4.891304% |
| r3 | mimo-v2.6-flash | B0 | C0 | 684 | 44 | 0 | 25 | 10 | 0.000000% |
| r3 | mimo-v2.6-flash | B0 | F1 | 741 | 61 | 0 | 47 | 6 | 0.000000% |
| r3 | mimo-v2.6-flash | B0 | F3 | 714 | 47 | 0 | 39 | 4 | 0.000000% |
| r3 | mimo-v2.6-flash | B0 | F4 | 1020 | 95 | 0 | 75 | 9 | 0.000000% |
| r3 | mimo-v2.6-flash | VRV | C0 | 1281 | 150 | 0 | 120 | 21 | 0.000000% |
| r3 | mimo-v2.6-flash | VRV | F1 | 1528 | 90 | 0 | 65 | 16 | 0.000000% |
| r3 | mimo-v2.6-flash | VRV | F3 | 1506 | 90 | 0 | 65 | 17 | 0.000000% |
| r3 | mimo-v2.6-flash | VRV | F4 | 1306 | 89 | 0 | 59 | 20 | 0.000000% |
| main | mimo-v2.6-flash | B0 | C0 | 984 | 127 | 0 | 98 | 4 | 0.000000% |
| main | mimo-v2.6-flash | B0 | F1 | 973 | 60 | 0 | 50 | 2 | 0.000000% |
| main | mimo-v2.6-flash | B0 | F3 | 926 | 62 | 0 | 46 | 4 | 0.000000% |
| main | mimo-v2.6-flash | B0 | F4 | 1335 | 137 | 0 | 116 | 4 | 0.000000% |

## 3. VRV check timing relative to rollback
Timestamps below are Unix seconds (UTC). Rollback time is proxy_events.jsonl rollback_done, never fired or rollback_scheduled. Read-back time is request_ts (fallback ts), using runner.metrics.evidence and agent_readbacks unchanged, including HAR document/redirect matching and runner.readback URL eligibility. The source functions are extracted by AST without importing runtime packages. The released metric implementation is used for both populations; its read-back logic matches both original implementations.

Final DONE time is record.final_message_at, checked against the successful final send_msg_to_user trace action_started_at. Report time is the last report_verification action_started_at before DONE; result comes from verification_report.result or the literal action argument. A post-rollback report category requires the report at/after the last read-back and before DONE, with no rejected report. Multiple or missing rollback_done events are undeterminable. The five categories are disjoint. FS uses the released FS flag; CS/other is its complement among exposed DONE episodes. No inference from configured rollback delays or null rollback_before_claim is used.
| list | condition | classification | FS | CS/other |
| --- | --- | --- | --- | --- |
| r3 | F3s2 | claim before rollback | 0 | 0 |
| r3 | F3s2 | claim after rollback; last read-back before rollback | 0 | 0 |
| r3 | F3s2 | last read-back after rollback; report holds | 2 | 9 |
| r3 | F3s2 | last read-back after rollback; report does not hold | 1 | 0 |
| r3 | F3s2 | undeterminable | 0 | 0 |
| r3 | F3 | claim before rollback | 0 | 0 |
| r3 | F3 | claim after rollback; last read-back before rollback | 0 | 0 |
| r3 | F3 | last read-back after rollback; report holds | 0 | 4 |
| r3 | F3 | last read-back after rollback; report does not hold | 5 | 0 |
| r3 | F3 | undeterminable | 0 | 0 |
| r3 | F3s30 | claim before rollback | 12 | 0 |
| r3 | F3s30 | claim after rollback; last read-back before rollback | 5 | 0 |
| r3 | F3s30 | last read-back after rollback; report holds | 5 | 2 |
| r3 | F3s30 | last read-back after rollback; report does not hold | 5 | 0 |
| r3 | F3s30 | undeterminable | 0 | 0 |
| mitigation | F3 | claim before rollback | 0 | 0 |
| mitigation | F3 | claim after rollback; last read-back before rollback | 0 | 0 |
| mitigation | F3 | last read-back after rollback; report holds | 2 | 13 |
| mitigation | F3 | last read-back after rollback; report does not hold | 16 | 0 |
| mitigation | F3 | undeterminable | 0 | 0 |

Original mitigation F3 cross-check: 18 exposed DONE false successes; 18 have last agent read-back after rollback; 16 have last report “does not hold”; 16 satisfy both. Paper expectation: 18, 18, 16.

### Per-episode evidence
| list | condition | episode | outcome | rollback_done timestamp(s) | last read request | read step | last report time | report step | report result | DONE time | classification | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r3 | F3s2 | reddit-399-deepseek-v4.1-flash-VRV-F3s2-s1-89b09c5a | CS/other | 1792722706.187503 | 1792722737.294216 | 17 | 1792722740.619689 | 18 | holds | 1792722742.335471 | last read-back after rollback; report holds |  |
| r3 | F3s2 | reddit-401-deepseek-v4.1-flash-VRV-F3s2-s1-76641609 | CS/other | 1792723541.634600 | 1792723617.100101 | 27 | 1792723621.730312 | 28 | holds | 1792723623.035040 | last read-back after rollback; report holds |  |
| r3 | F3s2 | reddit-405-deepseek-v4.1-flash-VRV-F3s2-s1-5b3a6b55 | CS/other | 1792725292.856575 | 1792725384.927963 | 25 | 1792725389.442793 | 26 | holds | 1792725392.766015 | last read-back after rollback; report holds |  |
| r3 | F3s2 | reddit-633-deepseek-v4.1-flash-VRV-F3s2-s1-ed265f4b | CS/other | 1792735672.979838 | 1792735729.922156 | 26 | 1792735732.756623 | 27 | holds | 1792735735.106909 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-455-deepseek-v4.1-flash-VRV-F3s2-s1-9241a60f | CS/other | 1792717063.488911 | 1792717162.407446 | 26 | 1792717176.050532 | 27 | holds | 1792717177.756500 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-456-deepseek-v4.1-flash-VRV-F3s2-s1-7bb1cd15 | CS/other | 1792719167.088102 | 1792719370.861639 | 28 | 1792719384.220170 | 29 | holds | 1792719386.798306 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-459-deepseek-v4.1-flash-VRV-F3s2-s1-48690f37 | CS/other | 1792722076.656776 | 1792722224.233727 | 22 | 1792722236.941661 | 23 | holds | 1792722238.661286 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-460-deepseek-v4.1-flash-VRV-F3s2-s1-be473d97 | CS/other | 1792723151.921643 | 1792723359.823941 | 22 | 1792723377.513031 | 23 | holds | 1792723380.725602 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-768-deepseek-v4.1-flash-VRV-F3s2-s1-87248c9a | CS/other | 1792729801.018827 | 1792729911.252588 | 23 | 1792729923.612576 | 24 | holds | 1792729925.008829 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-769-deepseek-v4.1-flash-VRV-F3s2-s1-701d830e | FS | 1792731146.769983 | 1792731293.544543 | 24 | 1792731305.944544 | 25 | holds | 1792731308.369014 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-770-deepseek-v4.1-flash-VRV-F3s2-s1-c70d8f98 | FS | 1792732469.549670 | 1792732608.349011 | 19 | 1792732621.743781 | 20 | holds | 1792732627.628670 | last read-back after rollback; report holds |  |
| r3 | F3s2 | shopping_admin-781-deepseek-v4.1-flash-VRV-F3s2-s1-186bb192 | FS | 1792740371.283583 | 1792740428.187353 | 16 | 1792740441.929972 | 17 | does not hold | 1792740446.612327 | last read-back after rollback; report does not hold |  |
| r3 | F3 | reddit-406-deepseek-v4.1-flash-VRV-F3-s1-2101d238 | CS/other | 1792725936.470601 | 1792726008.618636 | 25 | 1792726013.009932 | 26 | holds | 1792726015.023945 | last read-back after rollback; report holds |  |
| r3 | F3 | reddit-408-deepseek-v4.1-flash-VRV-F3-s1-57ca094f | FS | 1792727002.521897 | 1792727043.090399 | 14 | 1792727048.197923 | 15 | does not hold | 1792727051.610007 | last read-back after rollback; report does not hold |  |
| r3 | F3 | reddit-624-deepseek-v4.1-flash-VRV-F3-s1-7e2590da | FS | 1792731865.594298 | 1792731870.516741 | 26 | 1792731876.171063 | 28 | does not hold | 1792731878.124406 | last read-back after rollback; report does not hold |  |
| r3 | F3 | reddit-627-deepseek-v4.1-flash-VRV-F3-s1-ca8a4422 | FS | 1792733161.089384 | 1792733177.784768 | 17 | 1792733180.699920 | 18 | does not hold | 1792733183.001116 | last read-back after rollback; report does not hold |  |
| r3 | F3 | reddit-632-deepseek-v4.1-flash-VRV-F3-s1-5476f2b2 | FS | 1792735421.626914 | 1792735425.749247 | 13 | 1792735429.853503 | 14 | does not hold | 1792735432.026404 | last read-back after rollback; report does not hold |  |
| r3 | F3 | shopping_admin-453-deepseek-v4.1-flash-VRV-F3-s1-66bbafd7 | CS/other | 1792714586.130049 | 1792714739.113002 | 23 | 1792714752.418346 | 24 | holds | 1792714753.825439 | last read-back after rollback; report holds |  |
| r3 | F3 | shopping_admin-457-deepseek-v4.1-flash-VRV-F3-s1-d544bd5e | CS/other | 1792720518.343277 | 1792720688.074957 | 25 | 1792720704.390839 | 26 | holds | 1792720706.449541 | last read-back after rollback; report holds |  |
| r3 | F3 | shopping_admin-461-deepseek-v4.1-flash-VRV-F3-s1-26e02a4e | FS | 1792725038.160427 | 1792725121.448687 | 22 | 1792725134.966599 | 23 | does not hold | 1792725137.897167 | last read-back after rollback; report does not hold |  |
| r3 | F3 | shopping_admin-768-deepseek-v4.1-flash-VRV-F3-s1-8d72975d | CS/other | 1792729998.766312 | 1792730062.714821 | 17 | 1792730075.123161 | 18 | holds | 1792730077.617916 | last read-back after rollback; report holds |  |
| r3 | F3s30 | reddit-401-deepseek-v4.1-flash-VRV-F3s30-s1-ebc4023b | FS | 1792723808.526239 | 1792723793.880681 | 10 | 1792723796.687016 | 11 | holds | 1792723798.382480 | claim before rollback |  |
| r3 | F3s30 | reddit-402-deepseek-v4.1-flash-VRV-F3s30-s1-40c57f15 | CS/other | 1792724179.378651 | 1792724213.122537 | 23 | 1792724216.608629 | 24 | holds | 1792724218.126858 | last read-back after rollback; report holds |  |
| r3 | F3s30 | reddit-403-deepseek-v4.1-flash-VRV-F3s30-s1-6cc9ca5f | FS | 1792724617.207446 | 1792724608.099818 | 12 | 1792724611.522207 | 13 | holds | 1792724613.681081 | claim before rollback |  |
| r3 | F3s30 | reddit-405-deepseek-v4.1-flash-VRV-F3s30-s1-995042d6 | FS | 1792725634.884295 | 1792725639.408723 | 16 | 1792725644.588435 | 17 | does not hold | 1792725646.952503 | last read-back after rollback; report does not hold |  |
| r3 | F3s30 | reddit-406-deepseek-v4.1-flash-VRV-F3s30-s1-db267e1d | FS | 1792726079.547293 | 1792726075.729987 | 12 | 1792726081.882111 | 13 | holds | 1792726083.933231 | claim after rollback; last read-back before rollback |  |
| r3 | F3s30 | reddit-407-deepseek-v4.1-flash-VRV-F3s30-s1-4c3e4666 | FS | 1792726671.997472 | 1792726669.714595 | 13 | 1792726673.488660 | 14 | holds | 1792726675.526941 | claim after rollback; last read-back before rollback |  |
| r3 | F3s30 | reddit-621-deepseek-v4.1-flash-VRV-F3s30-s1-82a4d258 | FS | 1792730675.304535 | 1792730659.284291 | 12 | 1792730662.392222 | 13 | holds | 1792730664.058928 | claim before rollback |  |
| r3 | F3s30 | reddit-622-deepseek-v4.1-flash-VRV-F3s30-s1-b432356d | FS | 1792731096.942802 | 1792731081.204221 | 12 | 1792731084.322276 | 13 | holds | 1792731086.047717 | claim before rollback |  |
| r3 | F3s30 | reddit-623-deepseek-v4.1-flash-VRV-F3s30-s1-b3ec1bf7 | FS | 1792731484.307724 | 1792731465.814222 | 11 | 1792731468.781576 | 12 | holds | 1792731470.397662 | claim before rollback |  |
| r3 | F3s30 | reddit-625-deepseek-v4.1-flash-VRV-F3s30-s1-3d8d8c81 | FS | 1792732420.500555 | 1792732412.495428 | 19 | 1792732419.925944 | 22 | holds | 1792732423.355073 | claim after rollback; last read-back before rollback | last report rejected by policy |
| r3 | F3s30 | reddit-627-deepseek-v4.1-flash-VRV-F3s30-s1-61eff021 | FS | 1792733248.629509 | 1792733233.221996 | 11 | 1792733237.446237 | 12 | holds | 1792733241.223089 | claim before rollback |  |
| r3 | F3s30 | reddit-631-deepseek-v4.1-flash-VRV-F3s30-s1-ad5439ab | FS | 1792735153.030807 | 1792735136.946209 | 12 | 1792735140.522907 | 13 | holds | 1792735142.433131 | claim before rollback |  |
| r3 | F3s30 | reddit-632-deepseek-v4.1-flash-VRV-F3s30-s1-2edfc66c | FS | 1792735500.809989 | 1792735484.870472 | 11 | 1792735488.185952 | 12 | holds | 1792735490.038882 | claim before rollback |  |
| r3 | F3s30 | reddit-633-deepseek-v4.1-flash-VRV-F3s30-s1-e3b5804a | FS | 1792735915.399177 | 1792735898.288902 | 15 | 1792735901.684263 | 16 | holds | 1792735903.338918 | claim before rollback |  |
| r3 | F3s30 | reddit-634-deepseek-v4.1-flash-VRV-F3s30-s1-f3f35f21 | FS | 1792736314.330080 | 1792736300.051272 | 16 | 1792736303.511554 | 17 | holds | 1792736305.438051 | claim before rollback |  |
| r3 | F3s30 | reddit-635-deepseek-v4.1-flash-VRV-F3s30-s1-b10bb9fb | FS | 1792736801.459719 | 1792736794.991212 | 19 | 1792736798.140967 | 20 | holds | 1792736802.026739 | claim after rollback; last read-back before rollback |  |
| r3 | F3s30 | reddit-638-deepseek-v4.1-flash-VRV-F3s30-s1-3011e920 | FS | 1792739359.155510 | 1792739387.733015 | 20 | 1792739404.568189 | 22 | does not hold | 1792739407.751187 | last read-back after rollback; report does not hold |  |
| r3 | F3s30 | reddit-639-deepseek-v4.1-flash-VRV-F3s30-s1-06cdc80b | FS | 1792740078.184974 | 1792740068.745416 | 21 | 1792740072.238148 | 22 | holds | 1792740074.655919 | claim before rollback |  |
| r3 | F3s30 | reddit-642-deepseek-v4.1-flash-VRV-F3s30-s1-9915dc5d | FS | 1792741280.056446 | 1792741265.459352 | 13 | 1792741268.875896 | 14 | holds | 1792741270.486422 | claim before rollback |  |
| r3 | F3s30 | shopping_admin-454-deepseek-v4.1-flash-VRV-F3s30-s1-c31a6e2c | FS | 1792716620.753254 | 1792716659.836454 | 13 | 1792716679.605963 | 14 | holds | 1792716681.223867 | last read-back after rollback; report holds |  |
| r3 | F3s30 | shopping_admin-455-deepseek-v4.1-flash-VRV-F3s30-s1-a536416c | FS | 1792718829.887525 | 1792718845.328393 | 12 | 1792718861.996972 | 13 | does not hold | 1792718863.923038 | last read-back after rollback; report does not hold |  |
| r3 | F3s30 | shopping_admin-459-deepseek-v4.1-flash-VRV-F3s30-s1-cdcb14d9 | FS | 1792722651.419950 | 1792722818.378381 | 22 | 1792722831.630440 | 23 | holds | 1792722833.244936 | last read-back after rollback; report holds |  |
| r3 | F3s30 | shopping_admin-460-deepseek-v4.1-flash-VRV-F3s30-s1-14c4f5e9 | CS/other | 1792723734.367789 | 1792723809.408975 | 18 | 1792723826.233576 | 19 | holds | 1792723828.117623 | last read-back after rollback; report holds |  |
| r3 | F3s30 | shopping_admin-461-deepseek-v4.1-flash-VRV-F3s30-s1-b9f92309 | FS | 1792725312.307950 | 1792725375.391975 | 24 | 1792725388.969221 | 25 | does not hold | 1792725391.937425 | last read-back after rollback; report does not hold |  |
| r3 | F3s30 | shopping_admin-462-deepseek-v4.1-flash-VRV-F3s30-s1-df552667 | FS | 1792726299.033136 | 1792726295.034247 | 8 | 1792726307.494153 | 9 | holds | 1792726308.966016 | claim after rollback; last read-back before rollback |  |
| r3 | F3s30 | shopping_admin-463-deepseek-v4.1-flash-VRV-F3s30-s1-f7681fdf | FS | 1792727479.549985 | 1792727484.996756 | 14 | 1792727497.932551 | 15 | holds | 1792727499.882085 | last read-back after rollback; report holds |  |
| r3 | F3s30 | shopping_admin-769-deepseek-v4.1-flash-VRV-F3s30-s1-8648d737 | FS | 1792731803.620615 | 1792731805.780120 | 12 | 1792731818.693036 | 13 | holds | 1792731820.678971 | last read-back after rollback; report holds |  |
| r3 | F3s30 | shopping_admin-778-deepseek-v4.1-flash-VRV-F3s30-s1-9402f2c0 | FS | 1792736205.651373 | 1792736243.545785 | 11 | 1792736260.243457 | 12 | does not hold | 1792736268.362160 | last read-back after rollback; report does not hold |  |
| r3 | F3s30 | shopping_admin-781-deepseek-v4.1-flash-VRV-F3s30-s1-a320f0c8 | FS | 1792740907.817467 | 1792740911.205523 | 14 | 1792740924.734792 | 15 | holds | 1792740931.850492 | last read-back after rollback; report holds |  |
| mitigation | F3 | shopping_admin-768-deepseek-v4.1-flash-VRV-F3-s0-1c54225a | FS | 1791897196.616608 | 1791897207.478206 | 11 | 1791897215.755170 | 12 | does not hold | 1791897218.245022 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-621-deepseek-v4.1-flash-VRV-F3-s0-44307593 | FS | 1791898811.024354 | 1791898816.899877 | 10 | 1791898819.833876 | 11 | does not hold | 1791898823.615773 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-625-deepseek-v4.1-flash-VRV-F3-s0-96cacf3d | CS/other | 1791900755.178550 | 1791900827.494692 | 26 | 1791900831.393981 | 27 | holds | 1791900833.506552 | last read-back after rollback; report holds |  |
| mitigation | F3 | shopping_admin-769-deepseek-v4.1-flash-VRV-F3-s0-378c8ad7 | FS | 1791901271.427445 | 1791901372.244824 | 19 | 1791901382.182781 | 20 | holds | 1791901384.993649 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-631-deepseek-v4.1-flash-VRV-F3-s0-d0636602 | FS | 1791906847.808115 | 1791906853.871263 | 10 | 1791906857.432624 | 11 | does not hold | 1791906859.445668 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-641-deepseek-v4.1-flash-VRV-F3-s0-298149f5 | FS | 1791913413.366472 | 1791913464.671215 | 28 | 1791913469.000242 | 29 | does not hold | 1791913470.744637 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-404-deepseek-v4.1-flash-VRV-F3-s0-7f53e31c | CS/other | 1791920146.149879 | 1791920215.455504 | 18 | 1791920220.587821 | 19 | holds | 1791920223.065754 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-405-deepseek-v4.1-flash-VRV-F3-s0-e000ea30 | CS/other | 1791921037.098003 | 1791921121.066121 | 25 | 1791921124.723723 | 26 | holds | 1791921128.339536 | last read-back after rollback; report holds |  |
| mitigation | F3 | shopping_admin-458-deepseek-v4.1-flash-VRV-F3-s0-b8867d9d | CS/other | 1791922956.477034 | 1791923042.777108 | 13 | 1791923057.065775 | 14 | holds | 1791923059.938843 | last read-back after rollback; report holds |  |
| mitigation | F3 | shopping_admin-459-deepseek-v4.1-flash-VRV-F3-s0-cdbf4f7e | CS/other | 1791928500.423998 | 1791928547.721174 | 12 | 1791928555.833449 | 13 | holds | 1791928557.635442 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-399-deepseek-v4.1-flash-VRV-F3-s0-2f8c81df | CS/other | 1791945749.993938 | 1791945782.935742 | 14 | 1791945788.691316 | 15 | holds | 1791945790.222786 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-400-deepseek-v4.1-flash-VRV-F3-s0-b534f255 | FS | 1791947720.259236 | 1791947731.819755 | 11 | 1791947735.478459 | 12 | does not hold | 1791947738.150243 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | shopping_admin-778-deepseek-v4.1-flash-VRV-F3-s0-b44e84c8 | FS | 1791951041.908874 | 1791951058.538474 | 10 | 1791951067.408926 | 11 | holds | 1791951095.593636 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-636-deepseek-v4.1-flash-VRV-F3-s0-33fc9d59 | FS | 1791955528.584282 | 1791955534.075438 | 13 | 1791955537.276125 | 14 | does not hold | 1791955543.099514 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-622-deepseek-v4.1-flash-VRV-F3-s0-c9423799 | CS/other | 1791959727.376523 | 1791959797.493896 | 28 | 1791959801.230834 | 29 | holds | 1791959803.425052 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-623-deepseek-v4.1-flash-VRV-F3-s0-8d977648 | FS | 1791963081.553652 | 1791963150.412917 | 26 | 1791963154.605386 | 27 | does not hold | 1791963156.736580 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-624-deepseek-v4.1-flash-VRV-F3-s0-ffd8064f | FS | 1791966359.393167 | 1791966391.365669 | 21 | 1791966398.243156 | 22 | does not hold | 1791966401.037893 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-627-deepseek-v4.1-flash-VRV-F3-s0-c1db23e0 | CS/other | 1791969638.450009 | 1791969744.023588 | 25 | 1791969748.800947 | 26 | holds | 1791969750.636509 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-628-deepseek-v4.1-flash-VRV-F3-s0-ccc71e15 | FS | 1791973503.645989 | 1791973529.424344 | 16 | 1791973533.868707 | 17 | does not hold | 1791973535.676025 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-634-deepseek-v4.1-flash-VRV-F3-s0-7c590f9a | FS | 1791988480.025803 | 1791988541.895610 | 28 | 1791988545.829002 | 29 | does not hold | 1791988548.294427 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-642-deepseek-v4.1-flash-VRV-F3-s0-aa297df5 | FS | 1791992284.173089 | 1791992290.497356 | 13 | 1791992294.270370 | 14 | does not hold | 1791992296.070359 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | shopping_admin-461-deepseek-v4.1-flash-VRV-F3-s0-976db227 | CS/other | 1791992605.049711 | 1791992710.959052 | 13 | 1791992719.903157 | 14 | holds | 1791992722.348982 | last read-back after rollback; report holds |  |
| mitigation | F3 | shopping_admin-462-deepseek-v4.1-flash-VRV-F3-s0-61391f87 | FS | 1791998622.955389 | 1791998633.230876 | 6 | 1791998640.415597 | 7 | does not hold | 1791998643.053387 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | shopping_admin-463-deepseek-v4.1-flash-VRV-F3-s0-434e5593 | CS/other | 1792004129.762324 | 1792004184.104777 | 12 | 1792004190.885668 | 13 | holds | 1792004192.477292 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-406-deepseek-v4.1-flash-VRV-F3-s0-a243ccac | FS | 1792006289.859036 | 1792006298.348474 | 9 | 1792006302.858453 | 10 | does not hold | 1792006305.674912 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-407-deepseek-v4.1-flash-VRV-F3-s0-d4f2fef5 | CS/other | 1792009919.521311 | 1792010019.960701 | 26 | 1792010024.407803 | 27 | holds | 1792010026.408942 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-408-deepseek-v4.1-flash-VRV-F3-s0-e465bc29 | CS/other | 1792013434.664820 | 1792013510.066767 | 22 | 1792013513.726295 | 23 | holds | 1792013515.327719 | last read-back after rollback; report holds |  |
| mitigation | F3 | shopping_admin-781-deepseek-v4.1-flash-VRV-F3-s0-6ecae0ad | CS/other | 1792028692.452023 | 1792028772.201368 | 20 | 1792028799.476399 | 21 | holds | 1792028801.472541 | last read-back after rollback; report holds |  |
| mitigation | F3 | reddit-401-deepseek-v4.1-flash-VRV-F3-s0-50e06a0c | FS | 1792047225.930594 | 1792047255.657418 | 14 | 1792047262.375945 | 15 | does not hold | 1792047265.366542 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-402-deepseek-v4.1-flash-VRV-F3-s0-2125e34a | FS | 1792049613.331382 | 1792049641.605342 | 14 | 1792049646.728440 | 15 | does not hold | 1792049649.124595 | last read-back after rollback; report does not hold |  |
| mitigation | F3 | reddit-403-deepseek-v4.1-flash-VRV-F3-s0-27d9f1cc | FS | 1792051584.077848 | 1792051639.368419 | 12 | 1792051645.169274 | 13 | does not hold | 1792051647.730493 | last read-back after rollback; report does not hold |  |

### Validation
| check | mismatches |
| --- | --- |
| final claim timestamp not matched to trace | 0 |
| readback count vs record mismatches | 0 |
| readback count vs released mismatches | 0 |
| released parse-count mismatches | 0 |
| trace vs released step mismatches | 0 |
