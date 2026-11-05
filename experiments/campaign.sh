#!/usr/bin/env bash
# Run N labelled failure-injection experiments back to back and export each one.
# Usage: experiments/campaign.sh [first_seed] [last_seed]     (default 1..5, ~55 min per run)
set -u
cd "$(dirname "$0")"
for seed in $(seq "${1:-1}" "${2:-5}"); do
  run=$(printf 'run%02d' "$seed")
  [ -f "data/$run/metrics.csv" ] && { echo "$run already done"; continue; }
  python3 run_experiment.py --run-id "$run" --seed "$seed" || exit 1
  sleep 90   # let the last fault's tolerance window be scraped before exporting
  docker run --rm --network host -u "$(id -u):$(id -g)" -v "$PWD:/work" mtech-analysis:1.0 \
    python export.py "data/$run"
done
echo "campaign finished $(date)"
