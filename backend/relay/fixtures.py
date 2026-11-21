"""HAND-WRITTEN TEST DOUBLES. Not model output.

Used by the unit tests and by RELAY_MODE=fixture (UI development without an API key).
The content is adapted from the scripted reference concept demo. Anything produced in
fixture mode is labelled "fixture" in the UI, the ledger and the proof rail, and is
never used for eval numbers or for replays.
"""
from __future__ import annotations

FIXTURE_MODEL = "fixture (hand-written, not a model)"

_Q_TP = ("At 6th dose: pre-dose and 0.5, 1, 2 (end of infusion), 6, 48, 144 (Day 76), 240 (Day 80), 336 "
         "(Day 84), 480 (Day 90), and 840 h (Day 105) after the start of the 6th administration.")
_Q_SUB = ("TJ301 PK will be assessed in a subgroup of patients in Mainland China (24 patients, 8 per arm; "
          "electronically assigned at randomisation and consenting to the extra procedures).")
_Q_48 = "the time window of 48 h post dosing is ‘theoretical timepoint ±1 hour,"
_Q_NONE = "At Visits 2.1, 2.2, 2.3, 7.1, 7.2, 7,3, and 8.1, no other data will be collected."

DIFF = {
    "protocol_id": "CTJ301UC201", "version": "1.1", "previous_version": "1.0",
    "spoken_summary": "Version 1.1 adds seven pharmacokinetic sampling visits for the Mainland China PK subgroup. "
                      "Nothing else in the visit schedule changes.",
    "changes": [
        {"id": "CH-1", "section": "6.2.5 PK Visits", "page": 51, "quote": _Q_TP, "type": "schedule",
         "summary": "New PK sampling visits V2.1, V2.2, V2.3, V7.1, V7.2, V7.3 and V8.1 at 48, 144, 240 and 480 h after dosing.",
         "affects_edc": True, "edc_note": "Seven new events with the PK form."},
        {"id": "CH-2", "section": "6.2.5 PK Visits", "page": 51, "quote": _Q_SUB, "type": "schedule",
         "summary": "The new visits apply only to the Mainland China PK subgroup, assigned at randomisation.",
         "affects_edc": True, "edc_note": "Needs a dynamic rule on the randomisation form."},
        {"id": "CH-3", "section": "6.2.5 PK Visits", "page": 51, "quote": _Q_48, "type": "procedure",
         "summary": "The 48 h sample must be taken within one hour of the theoretical time.",
         "affects_edc": True, "edc_note": "Edit check on actual sampling time."},
        {"id": "CH-4", "section": "6.2.5 PK Visits", "page": 51, "quote": _Q_NONE, "type": "procedure",
         "summary": "No other data are collected at the new visits.", "affects_edc": True, "edc_note": "Only the PK form is assigned."},
        {"id": "CH-5", "section": "Version of protocol or amendment", "page": 5, "quote": "Ver.1.1", "type": "admin",
         "summary": "Protocol version and date updated.", "affects_edc": False, "edc_note": ""},
    ],
}

OPTIONS = [
    {"id": "A", "name": "Seven scheduled PK visits, shown by a dynamic rule",
     "summary": "Add V2.1 to V8.1 as their own events and create them only for PK-substudy participants.",
     "pros": ["Each sample has a due date, so a missed sample shows as overdue", "Existing PK form is reused unchanged"],
     "cons": ["Seven more events to maintain", "Sites see more visits in the schedule"],
     "tradeoff": "Option A matches Table 1 exactly and makes every missed sample visible. It costs seven extra events in the build.",
     "recommended": True},
    {"id": "B", "name": "One unscheduled, repeating PK visit",
     "summary": "Add one repeating event group that the site adds for each extra sample.",
     "pros": ["Small build", "Flexible if sampling times change again"],
     "cons": ["Nothing is expected, so a missed sample does not show as overdue", "Day windows cannot be checked per visit"],
     "tradeoff": "Option B is a smaller build that sites control themselves. It cannot show a missed sample as overdue.",
     "recommended": False},
]


def _ev(n, tid, label, group, day, w, page=44):
    return {"op_id": f"OP-{n:02d}", "kind": "add_event", "target_id": tid, "label": label, "group": group, "day": day,
            "window_before": w, "window_after": w, "dynamic": True, "source_change_ids": ["CH-1"], "source_page": 51,
            "source_quote": _Q_TP, "basis": "copied", "rationale": f"{tid} is a PK sampling visit on Day {day}.",
            "uncertainty": "" if tid != "V7.2" else "Table 1 gives no day window for Day 76 although Section 6.2.5 allows ±24 h."}


def _check48(n, ev, dose_ev):
    return {"op_id": f"OP-{n:02d}", "kind": "add_rule", "target_id": f"PK_48H_{ev.replace('.', '_')}",
            "rule": {"type": "edit_check", "description": f"48 h PK sample at {ev} must be within 1 h of dose + 48 h",
                     "when": f"abs((PK.PKDTC - ${dose_ev}.EX.EXSTDTC) - 48h) > 1h",
                     "then": {"action": "open_query", "message": "PK sample is outside the 48 h ± 1 h window (protocol 6.2.5)."},
                     "scope": {"form": "PK", "events": [ev]}},
            "source_change_ids": ["CH-3"], "source_page": 51, "source_quote": _Q_48, "basis": "interpreted",
            "rationale": "The 48 h sample has a window in hours, so the check uses the actual sampling time.", "uncertainty": ""}


NEW = ["V2.1", "V2.2", "V2.3", "V7.1", "V7.2", "V7.3", "V8.1"]

BUILD_A = {
    "option_id": "A",
    "ops": [
        _ev(1, "V2.1", "V2.1 (PK 48 h)", "TRT", 2, None), _ev(2, "V2.2", "V2.2 (PK 144 h)", "TRT", 6, 1),
        _ev(3, "V2.3", "V2.3 (PK 240 h)", "TRT", 10, 1), _ev(4, "V7.1", "V7.1 (PK 48 h)", "TRT", 72, None),
        _ev(5, "V7.2", "V7.2 (PK 144 h)", "TRT", 76, None), _ev(6, "V7.3", "V7.3 (PK 240 h)", "TRT", 80, 1),
        _ev(7, "V8.1", "V8.1 (PK 480 h)", "FU", 90, 2),
        {"op_id": "OP-08", "kind": "add_rule", "target_id": "DYN_PK_EVENTS",
         "rule": {"type": "dynamic", "description": "Add the 7 PK events for PK-substudy participants",
                  "when": '$V2.RAND.PKSUB = "Yes"', "then": {"action": "add_events", "events": NEW}},
         "source_change_ids": ["CH-2"], "source_page": 51, "source_quote": _Q_SUB, "basis": "interpreted",
         "rationale": "The subgroup is assigned at randomisation; RAND.PKSUB already records it.", "uncertainty": ""},
        {"op_id": "OP-09", "kind": "assign_form", "target_id": "PK", "event_ids": NEW, "source_change_ids": ["CH-4"],
         "source_page": 51, "source_quote": _Q_NONE, "basis": "copied", "rationale": "Only PK is collected at the new visits.", "uncertainty": ""},
        {"op_id": "OP-10", "kind": "add_codelist_entries", "target_id": "PKTPT", "entries": ["48 h", "144 h", "240 h", "480 h"],
         "source_change_ids": ["CH-1"], "source_page": 51, "source_quote": _Q_TP, "basis": "copied",
         "rationale": "The PK form takes its nominal timepoint from this codelist.", "uncertainty": ""},
        _check48(11, "V2.1", "V2"), _check48(12, "V7.1", "V7"),
        {"op_id": "OP-13", "kind": "update_review_plan", "target_id": "SDV100", "form_id": "PK", "event_ids": NEW,
         "source_change_ids": [], "source_page": None, "source_quote": "", "basis": "inferred",
         "rationale": "PK is 100% SDV at every existing event, so the same is carried over.",
         "uncertainty": "Not stated in the protocol."},
    ],
    "subjects": [
        {"id": "T-01", "description": "PK substudy = Yes, 48 h sample taken 48 h 30 min after the dose",
         "exercises": ["DYN_PK_EVENTS", "PK_48H_V2_1"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "Yes"}, "EX": {"EXSTDTC": "2017-06-01T09:00"}},
                  "V2.1": {"date": "2017-06-03", "PK": {"PKDTC": "2017-06-03T09:30"}}},
         "expect": {"events_added": NEW, "queries": []}},
        {"id": "T-02", "description": "PK substudy = No", "exercises": ["DYN_PK_EVENTS"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "No"}}}, "expect": {"events_added": [], "queries": []}},
        {"id": "T-03", "description": "Still in screening, PK substudy not answered", "exercises": ["DYN_PK_EVENTS"],
         "data": {"V1": {"date": "2017-05-20"}}, "expect": {"events_added": [], "queries": []}},
        {"id": "T-04", "description": "48 h sample taken 50 h after the dose", "exercises": ["PK_48H_V2_1"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "Yes"}, "EX": {"EXSTDTC": "2017-06-01T09:00"}},
                  "V2.1": {"date": "2017-06-03", "PK": {"PKDTC": "2017-06-03T11:00"}}},
         "expect": {"events_added": NEW, "queries": ["PK_48H_V2_1"]}},
        {"id": "T-05", "description": "V2.2 dated 3 days late (window is ±1 day)", "exercises": ["WINDOW:V2.2"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "Yes"}}, "V2.2": {"date": "2017-06-10"}},
         "expect": {"events_added": NEW, "queries": ["WINDOW:V2.2"]}},
        {"id": "T-06", "description": "48 h sample after the sixth dose, 46 h after the dose", "exercises": ["PK_48H_V7_1"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "Yes"}}, "V7": {"date": "2017-08-10", "EX": {"EXSTDTC": "2017-08-10T10:00"}},
                  "V7.1": {"date": "2017-08-12", "PK": {"PKDTC": "2017-08-12T08:00"}}},
         "expect": {"events_added": NEW, "queries": ["PK_48H_V7_1"]}},
    ],
}

BUILD_B = {
    "option_id": "B",
    "ops": [
        {"op_id": "OP-01", "kind": "add_event_group", "target_id": "PKU", "label": "PK Sampling (unscheduled)", "repeating": True,
         "source_change_ids": ["CH-1"], "source_page": 51, "source_quote": _Q_TP, "basis": "interpreted",
         "rationale": "One reusable unscheduled visit instead of seven fixed ones.", "uncertainty": "A missed sample will not show as overdue."},
        {"op_id": "OP-02", "kind": "add_event", "target_id": "PKU1", "label": "PK Sampling", "group": "PKU", "day": None,
         "window_before": None, "window_after": None, "dynamic": True, "source_change_ids": ["CH-1"], "source_page": 51,
         "source_quote": _Q_TP, "basis": "interpreted", "rationale": "The repeating PK sampling event.", "uncertainty": ""},
        {"op_id": "OP-03", "kind": "add_rule", "target_id": "PK_UNSCHED_ALLOWED",
         "rule": {"type": "dynamic", "description": "Offer PK Sampling only to PK-substudy participants",
                  "when": '$V2.RAND.PKSUB = "Yes"', "then": {"action": "add_events", "events": ["PKU1"]}},
         "source_change_ids": ["CH-2"], "source_page": 51, "source_quote": _Q_SUB, "basis": "interpreted",
         "rationale": "Other participants should not be offered the visit.", "uncertainty": ""},
        {"op_id": "OP-04", "kind": "assign_form", "target_id": "PK", "event_ids": ["PKU1"], "source_change_ids": ["CH-4"],
         "source_page": 51, "source_quote": _Q_NONE, "basis": "copied", "rationale": "Only PK is collected.", "uncertainty": ""},
        {"op_id": "OP-05", "kind": "add_codelist_entries", "target_id": "PKTPT", "entries": ["48 h", "144 h", "240 h", "480 h"],
         "source_change_ids": ["CH-1"], "source_page": 51, "source_quote": _Q_TP, "basis": "copied",
         "rationale": "New nominal timepoints.", "uncertainty": ""},
    ],
    "subjects": [
        {"id": "T-01", "description": "PK substudy = Yes", "exercises": ["PK_UNSCHED_ALLOWED"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "Yes"}}}, "expect": {"events_added": ["PKU1"], "queries": []}},
        {"id": "T-02", "description": "PK substudy = No", "exercises": ["PK_UNSCHED_ALLOWED"],
         "data": {"V2": {"date": "2017-06-01", "RAND": {"PKSUB": "No"}}}, "expect": {"events_added": [], "queries": []}},
    ],
}

AUDIT = {
    "A": {"findings": [
        {"id": "F-1", "kind": "untraceable", "op_id": "OP-13", "change_id": None, "page": None, "severity": "medium",
         "detail": "Extending the 100% SDV review plan to the new visits is not stated anywhere in the protocol."},
        {"id": "F-2", "kind": "uncertain", "op_id": "OP-05", "change_id": "CH-1", "page": 44, "severity": "medium",
         "detail": "V7.2 has no day window in Table 1, but Section 6.2.5 gives the 144 h sample ±24 hours. V2.2, the same sample after the first dose, has ±1 day."},
    ], "overall_uncertainty": "The protocol is inconsistent about the Day 76 window."},
    "B": {"findings": [
        {"id": "F-1", "kind": "missing", "op_id": None, "change_id": "CH-3", "page": 51, "severity": "high",
         "detail": "The 48 h ± 1 h sampling window in Section 6.2.5 has no edit check in this option."},
    ], "overall_uncertainty": "Timing windows are not enforced."},
}
