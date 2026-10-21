# Validity flags (work in progress)
Contamination rule: Reddit and start within inclusive 2026-10-10T11:04:00Z–2026-10-11T06:56:00Z; residue_flag=in_window.
Search included ignored Python/Markdown/JSON files outside runs. No exact non-clean episode classifier/list was found. tools/state_audit_report.py joins proxy flow timestamps, URL entities, HAR selection/value evidence and pristine/live drift; its target/forum/read exposure lists are possible exposure, not exact front-page residue. Subscription drift has three forum IDs (10022, 10075, 10027), timestamp-matched cause candidates, and zero baseline witnesses. Reported 404/406/620 are not these forum IDs.
Starts: record.started_at, else baseline.episode_started_at; if both absent, earliest proxy request_ts/ts or trace.started_at (estimate, not a recorded episode start). Sources: {"earliest proxy/trace": 678, "record": 3431}.
Counts below are in_window/residue; N is selected study size. Shared B0 IDs belong to both studies; CSV uses study=main;mitigation and one row per unique ID.
Main by condition: C0=42/42 (N=255); F1=39/39 (N=256); F2=12/12 (N=100); F3=37/37 (N=255); F4=35/35 (N=255); F5=34/34 (N=255); F6=34/34 (N=255).
Mitigation B0: C0=11/11 (N=59); F1=9/9 (N=59); F3=10/10 (N=59); F4=9/9 (N=59); F5=9/9 (N=59); F6=8/8 (N=59).
Mitigation B1: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
Mitigation B2: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
Mitigation Oracle: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
Mitigation VRV: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
Mitigation VRV-modeltrigger: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
Mitigation VRV-nocbr: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
Mitigation VRV-samepage: C0=0/0 (N=59); F1=0/0 (N=59); F3=0/0 (N=59); F4=0/0 (N=59); F5=0/0 (N=59); F6=0/0 (N=59).
main by family: fam-12=21/21 (N=140); fam-13=55/55 (N=135); fam-15=0/0 (N=140); fam-16=0/0 (N=140); fam-22=70/70 (N=120); fam-241=0/0 (N=72); fam-242=0/0 (N=120); fam-247=0/0 (N=144); fam-251=0/0 (N=120); fam-4=87/87 (N=96); fam-6=0/0 (N=120); fam-6100=0/0 (N=140); fam-742=0/0 (N=144).
mitigation by family: fam-12=5/5 (N=240); fam-13=12/12 (N=240); fam-15=0/0 (N=240); fam-16=0/0 (N=240); fam-22=17/17 (N=240); fam-241=0/0 (N=144); fam-242=0/0 (N=240); fam-247=0/0 (N=288); fam-4=22/22 (N=192); fam-6=0/0 (N=240); fam-6100=0/0 (N=240); fam-742=0/0 (N=288).
Unique IDs=4109; window/residue=233; rerun_selected=242; selected superseded IDs=0. Reruns match a superseded record on site/task/model/policy/condition/seed; journal lacks explicit replacement links.
Reddit unique episodes=2669; n_delete_req>0=0; deletion requests=0; mentions_delete=23. Mentions use case-insensitive delet substring; requests use POST/DELETE and URL path containing delete.
main: deletion-request episodes=0, mentions_delete=5; mitigation: deletion-request episodes=0, mentions_delete=19.
Dialog field scan (recursive record/trace keys containing dialog): 0 fields in 0 episodes; no dialog counts recorded. No recorded fields does not establish no browser dialogs.
Prefixes include the triggering action: fault fired_step if recorded; otherwise first trace matched_writes or mutating request whose method+URL has proxy matched_entry equal to episode family; no match uses whole sequence. Actions are compared literally in step order, including the write step. Proxy matches avoid treating unrelated mutations as task writes.
Prefix SHA1 is first 12 hex characters of UTF-8 compact JSON action list; computed for every selected episode. Boundary sources: {"fired_step": 2580, "matched_write": 506, "whole_sequence": 1023}.
Main B0 identical groups overall: 8/256 (3.1%).
Identical deepseek-v4.1-flash: 1/64 (1.6%).
Identical mimo-v2.6-flash: 0/64 (0.0%).
Identical minimax-m3: 0/64 (0.0%).
Identical qwen3.7-plus: 7/64 (10.9%).
Identical reddit: 6/156 (3.8%).
Identical shopping_admin: 2/100 (2.0%).
Caveats: the time window is a conservative proxy for residue, not proof of altered outcomes; minute-only endpoint interpreted literally. Request/trace evidence cannot prove identical page state or model reasoning. Prefix groups combine all selected seeds across conditions; condition counts are unique conditions. Non-Reddit deletion fields are zero (not assessed). No live services, imports from the benchmark, or tests were run.
