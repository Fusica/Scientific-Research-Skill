# Personalization roadmap

This file records the second-round design questions; it does not activate unreviewed defaults.

## Domain profiles

### Reinforcement learning

Decide environment/version pinning, seed and episode policy, evaluation determinism, statistical unit, IQM/bootstrap reporting, sample efficiency, wall-clock/compute accounting, offline/online split, and baseline reproduction tolerance.

### LLM systems

Decide model/tokenizer/version capture, data lineage and contamination checks, training-token/FLOP accounting, prompting/decoding protocol, judge-model bias controls, human-evaluation protocol, cost/latency reporting, and closed-model reproducibility boundaries.

### UAV and control

Decide dynamics and estimator assumptions, control/sensor frequencies, latency/noise/wind models, simulator and PX4/ROS versions, calibration/ground-truth uncertainty, safety envelope, HIL and real-flight gates, tracking/collision/energy metrics, and sim-to-real protocol.

## Toolchain

Choose project defaults only after reviewing current practice:

- configuration: Hydra/OmegaConf or repository-native alternatives;
- tracking: W&B, MLflow, or local artifact registry;
- tuning: Optuna, Ray Tune, or explicit sweeps;
- execution: local, Docker, Slurm, cloud, or lab machines;
- robotics: ROS2, PX4, Gazebo, Isaac Sim, AirSim, MuJoCo, or other simulators;
- literature: Semantic Scholar, OpenAlex, arXiv, OpenReview, DBLP, CVF, IEEE/ACM access;
- manuscript: LaTeX build, citation validation, visual QA, and number traceability.

## Personal decision rules

Elicit and encode:

- preferred idea scoring and rejection criteria;
- acceptable compute and experiment risk;
- must-run versus optional evidence for each claim type;
- how failed and negative experiments are summarized;
- preferred manuscript style and terminology;
- reviewer triage rules;
- what project observations may become reusable memory.

## Venue profiles

Start with the venues actually targeted. Capture page/format rules, review form, artifact/anonymization policy, expected evidence, common reviewer concerns, rebuttal constraints, and submission checklist. Verify time-sensitive rules from official venue sources when used.
