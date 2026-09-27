"""Root-cause and action guidance for each defect pattern.

The content summarises widely used wafer-map failure-pattern interpretation from
yield engineering practice (spatial signature analysis). It gives *likely* causes
ranked roughly by how often they explain the pattern, and what to check to confirm
each one. It is decision support: an engineer confirms the cause with tool logs,
in-line inspection data and metrology before changing a process.

Each entry:
    summary     what the pattern looks like on the map
    mechanism   how it typically forms (plain language)
    causes      [{cause, area, check}]  likely cause, process area, what confirms it
    investigate ordered steps to find the actual cause
    prevent     changes that stop it coming back
Immediate actions depend on severity and are added by ``guidance()``.
"""
from __future__ import annotations

from waferguard.taxonomy import display_name

GUIDE: dict[str, dict] = {
    "Center": {
        "summary": "Failing dies concentrated in a disc at the wafer centre.",
        "mechanism": ("A process that varies with radius ran differently at the centre than at the edge: "
                      "film or resist ended up too thick or too thin there, or material was removed faster or "
                      "slower. Spin-on coatings, CMP and single-wafer deposition and etch are all "
                      "radius-dependent, which is why this pattern points at them first."),
        "causes": [
            {"cause": "CMP centre-fast or centre-slow removal", "area": "CMP",
             "check": "Post-CMP thickness map: is the centre out of spec? Carrier zone pressures and pad life."},
            {"cause": "Resist or spin-on film thickness non-uniform at centre", "area": "Lithography / track",
             "check": "Resist thickness map, dispense volume and nozzle position, spin speed and exhaust."},
            {"cause": "Deposition gas flow or showerhead problem", "area": "CVD / PVD",
             "check": "Film thickness and sheet-resistance maps; showerhead clogging, gas-flow and pressure logs."},
            {"cause": "Temperature hot or cold spot at the chuck centre", "area": "Thermal / etch",
             "check": "Chuck or heater zone temperatures, backside helium cooling, RTP lamp calibration."},
        ],
        "investigate": [
            "Compare this wafer's route: which tools did it pass that other, passing lots did not?",
            "Pull radial thickness or CD maps for this lot from metrology and look for a centre excursion.",
            "Check whether other wafers from the same CMP / coat / deposition chamber show the same disc.",
        ],
        "prevent": [
            "Add a centre-point limit to the radial uniformity SPC chart of the responsible tool.",
            "Tighten preventive maintenance on the identified part (pad, showerhead, heater zone).",
        ],
    },
    "Donut": {
        "summary": "A ring of failing dies at mid-radius, with good dies inside and outside it.",
        "mechanism": ("A ring means something changes at one particular radius: a boundary between heater or "
                      "pressure zones, a CMP carrier zone, or the point where coating flow changes behaviour. "
                      "The process is fine at the centre and the edge but off in that band."),
        "causes": [
            {"cause": "CMP multi-zone pressure imbalance", "area": "CMP",
             "check": "Carrier zone pressure settings and logs; thickness profile for a mid-radius step."},
            {"cause": "Heater or electrostatic-chuck zone boundary", "area": "Thermal / etch / deposition",
             "check": "Zone temperature logs and chuck zone calibration; compare against the ring radius."},
            {"cause": "Resist or film coating ring (air flow, dispense)", "area": "Lithography / track",
             "check": "Resist thickness map for a ring; cup exhaust and dispense settings."},
            {"cause": "Annular deposition or etch non-uniformity", "area": "CVD / etch",
             "check": "Film thickness and etch-rate maps; process kit and gas-distribution condition."},
        ],
        "investigate": [
            "Measure the ring radius and match it to known zone boundaries on the suspect tools.",
            "Pull radial metrology for the lot and look for a band at that radius.",
            "Check whether the ring appears only on wafers from one chamber or chuck.",
        ],
        "prevent": [
            "Re-balance and re-qualify the zone settings; add a mid-radius point to uniformity monitoring.",
        ],
    },
    "Edge-Ring": {
        "summary": "Failing dies all around the wafer edge.",
        "mechanism": ("The outer few millimetres of a wafer are the hardest to process uniformly: plasma, film "
                      "and polish behaviour change at the edge, and the edge bead of resist is removed there. "
                      "When the whole circumference fails, a process edge effect is far more likely than "
                      "handling, which would hit only one spot."),
        "causes": [
            {"cause": "Etch edge non-uniformity (worn focus or edge ring)", "area": "Etch",
             "check": "Edge-ring RF hours or wear, edge etch rate and CD versus centre."},
            {"cause": "Edge-bead removal (EBR) set wrong", "area": "Lithography / track",
             "check": "EBR width and solvent settings, resist edge profile under a microscope."},
            {"cause": "CMP edge roll-off", "area": "CMP",
             "check": "Thickness near the edge, retaining-ring wear and pressure."},
            {"cause": "Film peeling or delamination at the bevel", "area": "Deposition / clean",
             "check": "Bevel inspection for flaking; edge-exclusion settings in deposition."},
        ],
        "investigate": [
            "Inspect the bevel and edge region of a failing wafer (optical review or SEM).",
            "Check consumable life counters (focus ring, retaining ring) on the etch and CMP tools used.",
            "Compare edge-die yield across chambers to find the one producing the ring.",
        ],
        "prevent": [
            "Set replacement limits for edge consumables from the RF-hour or wafer count where rings start.",
            "Monitor edge-die yield as its own SPC metric.",
        ],
    },
    "Edge-Loc": {
        "summary": "A patch of failing dies at one part of the edge only.",
        "mechanism": ("Damage that hits the edge at one angle usually comes from something touching the wafer "
                      "there: a robot blade, clamp finger, lift pin or cassette slot. If the patch sits at the "
                      "same angle (relative to the notch) on many wafers, a contact point is almost certain."),
        "causes": [
            {"cause": "Wafer handling contact (robot blade, end effector, clamp)", "area": "Automation / handling",
             "check": "Is the patch at the same angle on other wafers? Inspect and re-teach robot blades and clamps."},
            {"cause": "Local edge contamination or residue", "area": "Wet clean / etch",
             "check": "Review the edge under a microscope for residue; check drain or splash in wet benches."},
            {"cause": "Chuck, clamp ring or lift-pin damage", "area": "Etch / deposition",
             "check": "Inspect the chuck and clamp ring for damage aligned with the failing angle."},
            {"cause": "Notch or alignment-related exposure issue", "area": "Lithography",
             "check": "Exposure and focus of edge fields near the notch; edge-field alignment marks."},
        ],
        "investigate": [
            "Note the angle of the patch relative to the notch and compare across recent failing wafers.",
            "If the angle repeats, trace which tools grip or support the wafer at that angle.",
            "Microscope review of the edge for mechanical marks versus residue.",
        ],
        "prevent": [
            "Add handling-contact inspection to robot and clamp maintenance; re-teach positions after service.",
        ],
    },
    "Local": {
        "summary": "One or a few clusters of failing dies inside the wafer, away from the edge.",
        "mechanism": ("A tight cluster means something affected a small area once: a particle or droplet that "
                      "landed during processing, a small scratch or bubble, or a defect on the photomask that "
                      "prints in the same place every exposure. Mask defects repeat at the same die position; "
                      "particles do not."),
        "causes": [
            {"cause": "Particle or droplet contamination during processing", "area": "Any process tool / cleanroom",
             "check": "Overlay with in-line defect inspection maps for this wafer; tool particle monitors."},
            {"cause": "Photomask (reticle) defect", "area": "Lithography",
             "check": "Does the cluster repeat at the same die position on other wafers? Inspect the reticle."},
            {"cause": "Local film defect (bubble, void, peeling)", "area": "Deposition / coating",
             "check": "SEM review of the cluster; resist dispense bubbles; film stress."},
            {"cause": "Probe-card or test issue on a few sites", "area": "Wafer test",
             "check": "Re-test the failing dies; check probe marks and card cleaning history."},
        ],
        "investigate": [
            "Overlay the wafer map with in-line defect inspection data to find which layer added the defect.",
            "Check whether the cluster repeats at the same position on other wafers (points to a reticle).",
            "Send a failing die from the cluster for SEM or failure analysis.",
        ],
        "prevent": [
            "Tighten particle monitoring on the tool identified; schedule reticle inspection if it repeats.",
        ],
    },
    "Random": {
        "summary": "Failing dies scattered across the whole wafer without a shape.",
        "mechanism": ("Scattered fails mean the cause acted everywhere at once, most often contamination spread "
                      "across the wafer, a process running near the edge of its window so that dies fail at "
                      "random, or a test problem. Because there is no spatial clue, trends across lots and "
                      "tools are the main evidence."),
        "causes": [
            {"cause": "Airborne or chemical contamination", "area": "Cleanroom / wet processing",
             "check": "Cleanroom particle counts and chemical bath age or change logs for the lot's timeframe."},
            {"cause": "Process running at the edge of its window", "area": "Integration",
             "check": "Parametric test data: are key parameters drifting towards spec limits for the lot?"},
            {"cause": "Tester or probe-card problem", "area": "Wafer test",
             "check": "Re-test on another tester or probe card; compare with other lots tested on the same setup."},
            {"cause": "Material or incoming wafer quality", "area": "Incoming material",
             "check": "Check substrate lot and supplier certificate; compare with other lots on the same substrate lot."},
        ],
        "investigate": [
            "Re-test a sample of failing dies to rule out the tester first; it is the quickest check.",
            "Compare defect rates of lots processed in the same time window across tools.",
            "Review parametric (e-test) data for a lot-wide drift.",
        ],
        "prevent": [
            "Add alarms on particle counts and bath age; keep tester correlation checks in the test flow.",
        ],
    },
    "Scratch": {
        "summary": "Failing dies along a straight or curved line.",
        "mechanism": ("Lines are almost always mechanical: something dragged across the surface. Curved arcs "
                      "usually come from CMP, where the wafer rotates against the pad and a hard particle "
                      "(slurry agglomerate, pad or conditioner debris) is dragged along. Straight lines usually "
                      "come from handling, such as a robot blade or wafer sliding in a cassette."),
        "causes": [
            {"cause": "CMP scratch from slurry agglomerate or pad debris", "area": "CMP",
             "check": "Is the line an arc? Check slurry filters, pad and conditioner-disk condition, post-CMP inspection."},
            {"cause": "Handling scratch (robot blade, cassette, tweezers)", "area": "Automation / handling",
             "check": "Is the line straight? Inspect blades and cassettes; review manual-handling steps for the lot."},
            {"cause": "Scratch during wafer transfer or cleaning brushes", "area": "Clean",
             "check": "Brush scrubber condition and pressure; brush replacement history."},
        ],
        "investigate": [
            "Look at the scratch shape on the map: arc suggests CMP, straight suggests handling.",
            "Optical or SEM review of the scratch to find the layer where it starts.",
            "Check CMP consumable logs (filters, pad, conditioner) around the processing time.",
        ],
        "prevent": [
            "Shorten slurry-filter and conditioner replacement intervals; add post-CMP scratch inspection.",
        ],
    },
    "Near-full": {
        "summary": "Almost every die on the wafer fails.",
        "mechanism": ("When nearly everything fails, the cause is gross rather than subtle: a step was skipped, "
                      "the wrong recipe ran, a tool faulted mid-process, or the wafer was not tested correctly. "
                      "These are usually quick to confirm from records, and can affect a whole lot."),
        "causes": [
            {"cause": "Wrong recipe, missed or repeated process step", "area": "Manufacturing execution",
             "check": "Compare the wafer's actual route and recipes in MES with the planned route."},
            {"cause": "Tool fault during processing", "area": "Any process tool",
             "check": "Tool alarms and event logs for the processing time (RF, vacuum, temperature, interlocks)."},
            {"cause": "Test setup failure (probe card, test program)", "area": "Wafer test",
             "check": "Re-test on a known-good setup; check the program version and probe-card alignment."},
            {"cause": "Severe contamination or misprocessing", "area": "Wet processing / integration",
             "check": "Chemical or bath errors logged for the lot; visual inspection of the wafer surface."},
        ],
        "investigate": [
            "Re-test the wafer on a known-good setup to rule out the tester.",
            "Check the MES route history and tool alarms for the lot before anything else.",
            "Check the other wafers in the same lot: a whole-lot failure points at a shared step.",
        ],
        "prevent": [
            "Add recipe and route verification interlocks in MES; alarm on tool faults mid-lot.",
        ],
    },
    "none": {
        "summary": "No failure pattern: failing dies, if any, are few and isolated.",
        "mechanism": "The wafer shows normal background yield loss with no spatial signature.",
        "causes": [],
        "investigate": [],
        "prevent": [],
    },
    # die-level classes (used when the system is retrained on SEM / optical die images)
    "scratch": {
        "summary": "A line-shaped mechanical mark on the die surface.",
        "mechanism": "Something hard was dragged across the surface: CMP debris or a handling contact.",
        "causes": [
            {"cause": "CMP scratch", "area": "CMP", "check": "Slurry filters, pad and conditioner condition."},
            {"cause": "Handling contact", "area": "Automation / handling", "check": "Robot blades, cassettes, manual steps."},
        ],
        "investigate": ["Find the layer where the scratch starts (cross-section or layer-by-layer review)."],
        "prevent": ["Tighten CMP consumable intervals and handling inspections."],
    },
    "particle": {
        "summary": "A foreign particle on or embedded in the die.",
        "mechanism": "A particle landed during a process step and blocked or disturbed the pattern.",
        "causes": [
            {"cause": "Tool-generated particles", "area": "Process tool", "check": "Tool particle monitor wafers, chamber clean age."},
            {"cause": "Cleanroom or chemical contamination", "area": "Facilities / wet", "check": "Particle counts, filter and bath changes."},
        ],
        "investigate": ["Use EDX on the particle to identify the material and its likely source."],
        "prevent": ["Shorten chamber clean intervals for the source tool."],
    },
    "void": {
        "summary": "Missing material inside a film or fill, such as a gap in a metal line or via.",
        "mechanism": "The fill or deposition did not close completely, or material migrated away later.",
        "causes": [
            {"cause": "Poor gap fill or plating", "area": "Deposition / plating", "check": "Deposition profile, plating bath chemistry."},
            {"cause": "Electromigration or stress voiding", "area": "Metallisation", "check": "Anneal conditions, stress measurements."},
        ],
        "investigate": ["Cross-section the void to see whether it formed during fill or afterwards."],
        "prevent": ["Adjust fill recipe or plating chemistry; add void monitoring on test structures."],
    },
    "pattern": {
        "summary": "The printed pattern is distorted, missing or misaligned.",
        "mechanism": "Exposure, focus or etch transfer went wrong for this feature.",
        "causes": [
            {"cause": "Focus or dose excursion", "area": "Lithography", "check": "Scanner focus and dose logs, CD metrology."},
            {"cause": "Etch transfer problem", "area": "Etch", "check": "Etch profile and CD after etch."},
        ],
        "investigate": ["Compare after-develop and after-etch CD to see which step changed the pattern."],
        "prevent": ["Tighten focus and dose control; check the reticle for defects."],
    },
    "bridge": {
        "summary": "Two features that should be separate are connected.",
        "mechanism": "Material remained between lines: under-etch, residue, or a particle or pattern defect joining them.",
        "causes": [
            {"cause": "Under-etch or residue", "area": "Etch / clean", "check": "Etch time and endpoint, post-etch clean."},
            {"cause": "Lithography defect (scumming)", "area": "Lithography", "check": "Develop process, resist residue."},
        ],
        "investigate": ["SEM of the bridge to see whether it is residue, metal or a particle."],
        "prevent": ["Adjust etch endpoint and clean; monitor line spacing on test structures."],
    },
    "open": {
        "summary": "A conductor is broken, so the circuit is open.",
        "mechanism": "A line or via is incomplete: over-etch, a void, a particle blocking deposition, or a crack.",
        "causes": [
            {"cause": "Over-etch or thinning", "area": "Etch", "check": "Etch rate and endpoint, line thickness."},
            {"cause": "Via or contact not filled", "area": "Deposition / plating", "check": "Via fill profile, resistance of via chains."},
        ],
        "investigate": ["Electrical fault isolation, then cross-section at the open."],
        "prevent": ["Monitor via-chain resistance; tighten fill and etch windows."],
    },
    "short": {
        "summary": "Two conductors that should be isolated are electrically connected.",
        "mechanism": "Conductive material or a defect crosses the insulator between them.",
        "causes": [
            {"cause": "Metal residue or stringer", "area": "Etch / CMP", "check": "Post-CMP residue, etch completeness."},
            {"cause": "Dielectric breakdown or thin insulator", "area": "Deposition", "check": "Dielectric thickness and quality."},
        ],
        "investigate": ["Hot-spot or thermal fault isolation, then cross-section."],
        "prevent": ["Tighten residue inspection after CMP; monitor dielectric thickness."],
    },
}

IMMEDIATE = {
    "Critical": [
        "Put the lot on hold and notify the process engineer on shift.",
        "Stop the suspect tool from running more product until the cause is understood, if the same pattern is repeating on it.",
        "Keep the failing wafer for failure analysis; do not rework or scrap yet.",
    ],
    "Major": [
        "Flag the lot for engineering review before it moves to the next step.",
        "Watch the next wafers from the same tool; escalate to Critical if the pattern repeats.",
    ],
    "Minor": [
        "Log it and keep the lot moving.",
        "Check the equipment's trend in Process control; act if the pattern starts repeating.",
    ],
    "None": ["No action needed. Continue normal monitoring."],
}


def guidance(label: str, severity: str, needs_review: bool = False, recurrence: dict | None = None) -> dict:
    """Guide for one result. ``recurrence`` (from the database) sharpens the advice."""
    g = GUIDE.get(label)
    if g is None:
        g = {"summary": f"Pattern '{label}'.", "mechanism": "No guidance has been written for this class yet.",
             "causes": [], "investigate": [], "prevent": []}
    immediate = list(IMMEDIATE.get(severity, IMMEDIATE["Minor"]))
    notes = []
    if recurrence:
        eq, lot = recurrence.get("equipment"), recurrence.get("lot")
        if eq and eq.get("same") and eq["same"] > 1:
            notes.append(f"{eq['same']} of the last {eq['total']} wafers on {eq['id']} show the same pattern. "
                         "Repetition on one tool strongly points at that tool.")
            if severity in ("Major", "Critical"):
                immediate.insert(0, f"Check tool {eq['id']}: this pattern is repeating on it.")
        if lot and lot.get("same") and lot["same"] > 1:
            notes.append(f"{lot['same']} wafers in lot {lot['id']} show this pattern, so the cause is shared "
                         "across the lot (a common step or tool), not a one-off.")
    if needs_review and label != "none":
        immediate.insert(0, "Have an engineer confirm the classification first: the model's confidence is low.")
    return {
        "label": label,
        "title": display_name(label),
        "summary": g["summary"],
        "mechanism": g["mechanism"],
        "causes": g["causes"],
        "actions": {"immediate": immediate, "investigate": g["investigate"], "prevent": g["prevent"]},
        "recurrence_notes": notes,
        "disclaimer": "Likely causes based on the defect pattern. Confirm with tool logs, in-line inspection "
                      "and metrology before changing the process.",
    }
