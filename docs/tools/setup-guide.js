const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, Table, TableRow, TableCell,
  WidthType, ShadingType, AlignmentType, LevelFormat, BorderStyle, PageBreak,
  Footer, PageNumber, ImageRun,
} = require("docx");

const FONT = "Calibri", MONO = "Consolas";
const W = 9638; // A4 text width in DXA with 2 cm margins (11906 - 2*1134)
const ACCENT = "1F4E79";

// ---------- helpers ----------
const run = (t, o = {}) => new TextRun({ text: t, font: FONT, size: 22, ...o });
// inline markup: **bold** and `code`
function rich(text, o = {}) {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).filter(Boolean).map((s) =>
    s.startsWith("**") ? run(s.slice(2, -2), { bold: true, ...o })
      : s.startsWith("`") ? new TextRun({ text: s.slice(1, -1), font: MONO, size: 20, color: "9C2B00", ...o })
        : run(s, o));
}
const p = (t, o = {}) => new Paragraph({ children: rich(t), spacing: { after: 120 }, ...o });
const h1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const h2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const h3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });
const bullet = (t, level = 0) => new Paragraph({ numbering: { reference: "bullets", level }, children: rich(t), spacing: { after: 60 } });
let stepRef = 0;
function steps(items) {
  const ref = `steps${stepRef++}`;
  numberingConfigs.push({ reference: ref, levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.",
    alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 460, hanging: 360 } } } }] });
  return items.map((t) => new Paragraph({ numbering: { reference: ref, level: 0 }, children: rich(t), spacing: { after: 80 } }));
}
// shaded code block, one paragraph per line
function code(lines) {
  const arr = lines.split("\n");
  return arr.map((l, i) => new Paragraph({
    children: [new TextRun({ text: l || " ", font: MONO, size: 18, color: "1E1E1E" })],
    shading: { type: ShadingType.CLEAR, color: "auto", fill: "F2F2F2" },
    indent: { left: 200, right: 200 },
    spacing: { before: i === 0 ? 80 : 0, after: i === arr.length - 1 ? 160 : 0, line: 260 },
    border: i === 0 ? { top: { style: BorderStyle.SINGLE, size: 4, color: "D0D0D0", space: 4 } }
      : i === arr.length - 1 ? { bottom: { style: BorderStyle.SINGLE, size: 4, color: "D0D0D0", space: 4 } } : undefined,
    keepLines: true, keepNext: i < arr.length - 1,
  }));
}
function note(t, kind = "Note") {
  const fill = kind === "Important" ? "FDECEA" : "E8F1FA", bar = kind === "Important" ? "C0392B" : "2E75B6";
  return new Paragraph({
    children: [run(`${kind}: `, { bold: true, color: bar }), ...rich(t)],
    shading: { type: ShadingType.CLEAR, color: "auto", fill },
    border: { left: { style: BorderStyle.SINGLE, size: 24, color: bar, space: 8 } },
    indent: { left: 200 }, spacing: { before: 80, after: 160 },
  });
}
const border = { style: BorderStyle.SINGLE, size: 4, color: "BFBFBF" };
function table(rows, widths) {
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((r, ri) => new TableRow({
      tableHeader: ri === 0,
      children: r.map((c, ci) => new TableCell({
        width: { size: widths[ci], type: WidthType.DXA },
        borders: { top: border, bottom: border, left: border, right: border },
        shading: ri === 0 ? { type: ShadingType.CLEAR, color: "auto", fill: ACCENT } : undefined,
        margins: { top: 60, bottom: 60, left: 100, right: 100 },
        children: [new Paragraph({ children: rich(c, ri === 0 ? { bold: true, color: "FFFFFF" } : {}) })],
      })),
    })),
  });
}
const gap = () => new Paragraph({ children: [], spacing: { after: 120 } });

const numberingConfigs = [{
  reference: "bullets", levels: [
    { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 460, hanging: 280 } } } },
    { level: 1, format: LevelFormat.BULLET, text: "◦", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 900, hanging: 280 } } } },
  ],
}];

// ---------- content ----------
const REPO = "https://github.com/chaitanya0807/devopsproject.git";
const body = [];
const add = (...x) => body.push(...x.flat());

// title block
add(
  new Paragraph({ spacing: { before: 1800, after: 200 }, children: [new TextRun({ text: "Setup and Installation Guide", font: FONT, size: 52, bold: true, color: ACCENT })] }),
  new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: "Change-Aware Multi-Signal Anomaly Correlation for Kubernetes", font: FONT, size: 30, color: "404040" })] }),
  new Paragraph({ spacing: { after: 600 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: ACCENT, space: 6 } }, children: [new TextRun({ text: "M.Tech Project: Observability Testbed, Failure Injection and Evaluation", font: FONT, size: 24, color: "595959" })] }),
  p("This guide explains, step by step, how to install and run the complete project on a **new computer**, from a fresh operating system to a working Kubernetes cluster with Prometheus, Loki, Grafana, the CI/CD pipeline collector, the failure-injection experiments and the CAMSAC evaluation."),
  p(`Source code: \`${REPO.replace(".git", "")}\``),
  p("Author: Chaitanya · Version 1.0 · November 2026"),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({ children: [new TextRun({ text: "Contents", font: FONT, size: 32, bold: true, color: ACCENT })], spacing: { after: 160 } }),
  ...["1. What you will build", "2. System requirements", "3. Install the required software",
    "4. Download the project", "5. Start the Kubernetes cluster", "6. Deploy everything with one command",
    "7. Verify the installation", "8. Open the web interfaces", "9. Configure the CI/CD pipeline collector",
    "10. Run the failure-injection experiments (Mid-sem)", "11. Evidence and screenshots",
    "12. Daily use: stop and start", "13. Troubleshooting", "14. Remove everything"]
    .map((t) => new Paragraph({ children: [run(t, { size: 24 })], spacing: { after: 100 }, indent: { left: 200 } })),
  gap(),
  note("Quick start for experienced users: install Docker, kubectl, minikube and Helm (Section 3), then run `git clone`, `minikube start`, `./scripts/setup.sh` and `./scripts/verify.sh` (Sections 4–7)."),
  new Paragraph({ children: [new PageBreak()] }),
);

// 1 overview
add(
  h1("1. What you will build"),
  p("The project has two parts that run on the same computer:"),
  bullet("**Kubernetes cluster (minikube)** runs the workload: the `sample-app` order service (2 replicas), its `payments` dependency, a load generator, and the in-cluster collectors (cAdvisor, kube-state-metrics, Promtail)."),
  bullet("**Docker Compose stack** runs storage and visualisation: Prometheus, Loki, Grafana, Node Exporter and the Python GitLab pipeline collector."),
  gap(),
  new Paragraph({ alignment: AlignmentType.CENTER, children: [new ImageRun({ type: "png", data: fs.readFileSync("architecture.png"), transformation: { width: 600, height: 471 } })] }),
  new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 }, children: [run("Figure 1: System architecture", { italics: true, size: 18, color: "595959" })] }),
  table([
    ["Component", "Where it runs", "Purpose"],
    ["sample-app, payments, loadgen", "minikube, namespace demo-app", "Monitored workload with fault-injection hooks"],
    ["cAdvisor, kube-state-metrics, Promtail", "minikube, namespace monitoring", "Pod metrics, Kubernetes object state, log shipping"],
    ["Prometheus", "Docker Compose (port 9090)", "Metrics storage and the B1 threshold alert rules"],
    ["Loki", "Docker Compose (port 3100)", "Log storage"],
    ["Grafana", "Docker Compose (port 3000)", "Dashboards"],
    ["Node Exporter", "Docker Compose (port 9100)", "Host metrics"],
    ["pipeline-collector", "Docker Compose (port 8000)", "GitLab CI/CD pipeline data"],
    ["experiments/*", "Host + analysis container", "Fault injection, CAMSAC, baselines, evaluation"],
  ], [3000, 2900, 3738]),
);

// 2 requirements
add(
  h1("2. System requirements"),
  table([
    ["Item", "Minimum", "Recommended"],
    ["Operating system", "Ubuntu 22.04 / 24.04 (64-bit)", "Ubuntu 24.04 or newer"],
    ["CPU", "4 cores", "8+ cores"],
    ["RAM", "6 GB", "8 GB or more"],
    ["Free disk", "25 GB", "40 GB"],
    ["Internet", "Required for installation and image downloads", ""],
    ["Account", "A user with sudo rights", ""],
  ], [2400, 3700, 3538]),
  gap(),
  note("The project was developed and tested on Ubuntu 26.04 with 12 CPU cores and 5 GB RAM. With less than 6 GB of RAM, close the browser tabs you don't need while the cluster is running.", "Note"),
  p("**Windows users:** install WSL2 with Ubuntu 24.04 (`wsl --install -d Ubuntu-24.04` in an administrator PowerShell) and follow this guide **inside the Ubuntu terminal**. Install Docker Engine inside WSL as shown in Step 3 (not Docker Desktop), so that the minikube network is reachable exactly as on Linux."),
  p("**macOS users:** Docker Desktop works, but the minikube IP is not reachable from the Mac itself. Everything inside Docker (Prometheus scraping minikube) still works; to open the sample app in a browser use `minikube service sample-app -n demo-app`."),
);

// 3 install tools
add(
  h1("3. Install the required software"),
  p("Open a terminal and run the following commands **in order**. Each step ends with a check command; compare its output before moving on."),
  h2("Step 3.1: Basic tools"),
  ...code("sudo apt update\nsudo apt install -y git curl ca-certificates python3"),
  p("Check: `git --version` and `python3 --version` print version numbers."),
  h2("Step 3.2: Docker Engine"),
  ...code("curl -fsSL https://get.docker.com -o get-docker.sh\nsudo sh get-docker.sh\nsudo usermod -aG docker $USER"),
  note("**Log out and log back in** (or restart the computer) after the last command, so that your user can run Docker without sudo.", "Important"),
  p("Check (after logging in again):"),
  ...code("docker run --rm hello-world\ndocker compose version"),
  p("You should see “Hello from Docker!” and a Compose version (v2.x)."),
  h2("Step 3.3: kubectl (Kubernetes command-line tool)"),
  ...code('curl -LO "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"\nsudo install -m 0755 kubectl /usr/local/bin/kubectl\nrm kubectl'),
  p("Check: `kubectl version --client`"),
  h2("Step 3.4: minikube (local Kubernetes)"),
  ...code("curl -LO https://storage.googleapis.com/minikube/releases/latest/minikube-linux-amd64\nsudo install minikube-linux-amd64 /usr/local/bin/minikube\nrm minikube-linux-amd64"),
  p("Check: `minikube version`"),
  h2("Step 3.5: Helm (Kubernetes package manager)"),
  ...code("curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"),
  p("Check: `helm version --short`"),
  note("Python libraries (Flask, pandas, scikit-learn, Drain3 …) are **not** installed on the host. They are installed automatically inside Docker images, so no `pip` is needed."),
);

// 4 get code
add(
  h1("4. Download the project"),
  ...code(`cd ~\ngit clone ${REPO} monitoring\ncd ~/monitoring`),
  p("All later commands are run from the `~/monitoring` folder. The most important folders are:"),
  table([
    ["Folder / file", "Contents"],
    ["`docker-compose.yml`", "Prometheus, Loki, Grafana, Node Exporter, pipeline-collector"],
    ["`k8s/app/`", "Sample application code (`app.py`), Dockerfile, Kubernetes manifests"],
    ["`k8s/monitoring/`", "cAdvisor, Prometheus access (RBAC), Helm values"],
    ["`prometheus/`", "Scrape configuration and `rules/baseline.yml` (B1 alerts)"],
    ["`grafana/provisioning/`", "Datasources and the System Monitoring dashboard"],
    ["`pipeline-collector/`", "Python GitLab pipeline collector"],
    ["`experiments/`", "Fault injection, CAMSAC, baselines, evaluation"],
    ["`scripts/`", "`setup.sh`, `verify.sh`, `teardown.sh`"],
    ["`docs/`", "Architecture, testing, research proposal, Mid-sem report"],
  ], [3000, 6638]),
);

// 5 start
add(
  h1("5. Start the Kubernetes cluster"),
  ...code("minikube start --driver=docker --memory=2500 --cpus=2\nkubectl get nodes"),
  p("The first start downloads about 1 GB and takes 3–5 minutes. The check should show one node named `minikube` with STATUS `Ready`."),
  note("If your computer has 8 GB or more RAM you may use `--memory=4096`. Do not go below 2200 MB."),
);

// 6 setup
add(
  h1("6. Deploy everything with one command"),
  ...code("cd ~/monitoring\nchmod +x scripts/*.sh experiments/*.sh\n./scripts/setup.sh"),
  p("The script is safe to run again at any time. It performs these steps automatically:"),
  ...steps([
    "Checks that docker, kubectl, minikube and helm are installed.",
    "Starts minikube if it is not running and enables metrics-server.",
    "Builds the `sample-app:1.1` image **inside** minikube.",
    "Deploys the sample app, payments service and load generator (namespace `demo-app`).",
    "Deploys cAdvisor and the read-only Prometheus service account; installs kube-state-metrics and Promtail with Helm (namespace `monitoring`).",
    "Exports the service-account token and cluster CA into `prometheus/k8s/` (this folder is git-ignored; never commit it).",
    "Writes the current minikube IP into `prometheus/prometheus.yml`.",
    "Waits for all pods, then starts the Docker Compose stack (`docker compose up -d --build`).",
  ]),
  p("The first run takes 5–10 minutes (image downloads). It ends with a list of URLs."),
);

// 7 verify
add(
  h1("7. Verify the installation"),
  ...code("./scripts/verify.sh"),
  p("Wait about 2 minutes after setup before running it, so that Prometheus and Loki have collected data. The expected last line is:"),
  ...code("Result: 15 passed, 0 failed"),
  table([
    ["Test", "What it checks"],
    ["T01–T04", "Node Ready, all pods running, sample app reachable"],
    ["T05", "All Prometheus scrape targets are UP"],
    ["T06–T10", "Data from Node Exporter, kube-state-metrics, cAdvisor, sample-app, CI collector"],
    ["T11–T12", "Loki is ready and contains sample-app logs"],
    ["T13–T15", "Grafana healthy, collector endpoint works, pipeline CSV written"],
  ], [1800, 7838]),
  gap(),
  p("The output is also saved to `evidence/verify-output.txt`."),
);

// 8 access
add(
  h1("8. Open the web interfaces"),
  table([
    ["Service", "Address", "Login"],
    ["Grafana", "http://localhost:3000", "admin / admin (you are asked to set a new password)"],
    ["Prometheus targets", "http://localhost:9090/targets", "none"],
    ["Prometheus alerts (B1)", "http://localhost:9090/alerts", "none"],
    ["Loki readiness", "http://localhost:3100/ready", "none"],
    ["Pipeline collector", "http://localhost:8000/metrics", "none"],
    ["Sample app", "http://<minikube ip>:30080/api/orders", "none (`minikube ip` prints the IP)"],
  ], [2500, 3800, 3338]),
  gap(),
  h2("Find the dashboard"),
  ...steps([
    "Log in to Grafana.",
    "Open **Dashboards → M.Tech Project → System Monitoring**.",
    "The six rows are: Host, Kubernetes cluster, Containers/pods, Sample application, CI/CD pipelines, Logs.",
  ]),
  h2("Forgot the Grafana password?"),
  ...code("docker exec grafana grafana cli admin reset-admin-password NewPassword123"),
);

// 9 collector
add(
  h1("9. Configure the CI/CD pipeline collector"),
  p("By default the collector reads the public GitLab project `gitlab-org/gitlab-runner`, which needs no token. To use **your own** GitLab project, create a file `.env` in `~/monitoring`:"),
  ...code("GITLAB_URL=https://gitlab.com\nGITLAB_PROJECT_ID=your-group/your-project\nGITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxx\nPOLL_INTERVAL=300"),
  p("The token needs the `read_api` scope (GitLab → Preferences → Access Tokens). Then restart the collector and print a summary:"),
  ...code("docker compose up -d pipeline-collector\ndocker compose run --rm pipeline-collector python collector.py --once"),
  note("`.env` is git-ignored. Never commit access tokens to GitHub.", "Important"),
);

// 10 experiments
add(
  h1("10. Run the failure-injection experiments (Mid-sem)"),
  p("This part produces the labelled dataset and the comparison of CAMSAC against Prometheus threshold alerts and the other baselines."),
  h2("Step 10.1: Build the analysis image"),
  ...code("cd ~/monitoring/experiments\ndocker build -t mtech-analysis:1.0 ."),
  h2("Step 10.2: Run the experiment campaign"),
  p("Each run takes about **55 minutes**: an 8-minute warm-up, then 8 faults (2 minutes each) in a random order with 3.5-minute cool-downs, plus 2 benign deployments. To run 5 runs (about 4.5 hours) in the background:"),
  ...code("cd ~/monitoring/experiments\nsetsid nohup ./campaign.sh 1 5 > data/campaign.log 2>&1 < /dev/null &\ntail -f data/campaign.log      # Ctrl+C stops watching, not the campaign"),
  note("Keep the computer switched on and prevent sleep/suspend while the campaign runs. A run that is interrupted must be deleted (`rm -rf data/runNN`) and started again.", "Important"),
  p("Each finished run creates `experiments/data/runNN/` with `labels.csv` (ground truth), `changes.jsonl` (CD change feed), `metrics.csv`, `alerts.csv` and `logs.jsonl`."),
  h2("Step 10.3: Evaluate"),
  ...code("cd ~/monitoring/experiments\ndocker run --rm --network host -u $(id -u):$(id -g) -e MPLCONFIGDIR=/tmp \\\n  -v $PWD:/work mtech-analysis:1.0 \\\n  python -W ignore evaluate.py data/run0* --out results"),
  p("Results are written to `experiments/results/`:"),
  bullet("`summary.md`: comparison table (precision, recall, F1, false alarms per hour, window FPR, MTTD, modelled MTTR, root-cause top-1/top-3) and the Wilcoxon test."),
  bullet("`per_fault.csv`: one row per fault and method."),
  bullet("`timeline_runNN.png`: signal scores, change events, CAMSAC probability and B1 alerts over time."),
  bullet("`weights.json`: the learned CAMSAC fusion weights and thresholds."),
  h2("Step 10.4: Try a single fault manually (demo)"),
  ...code("kubectl -n demo-app set env deploy/sample-app ERROR_RATE=0.5    # error spike\nkubectl -n demo-app set env deploy/sample-app ERROR_RATE=0.02   # back to normal"),
  p("Watch the **Error rate** panel in Grafana and the **HighErrorRate** alert at http://localhost:9090/alerts."),
);

// 11 screenshots
add(
  h1("11. Evidence and screenshots"),
  p("Save screenshots in `evidence/screenshots/` with these names:"),
  table([
    ["File", "What to capture"],
    ["01-kubectl-pods.png", "`kubectl get pods -A`, all Running"],
    ["02-prometheus-targets.png", "http://localhost:9090/targets, all UP"],
    ["03-grafana-host-cluster.png", "Dashboard: Host and Kubernetes rows"],
    ["04-grafana-pods-app.png", "Dashboard: Containers and Sample application rows"],
    ["05-grafana-cicd-logs.png", "Dashboard: CI/CD and Logs rows"],
    ["06-loki-explore.png", "Explore → Loki → `{namespace=\"demo-app\"} |= \"error\"`"],
    ["07-collector-output.png", "`collector.py --once` output"],
    ["08-verify-script.png", "`./scripts/verify.sh` with 15/15 passed"],
    ["09-error-injection.png", "Error-rate panel during the Step 10.4 demo"],
    ["10-helm-compose.png", "`helm list -A` and `docker compose ps`"],
  ], [3400, 6238]),
  gap(),
  p("Then upload them to GitHub:"),
  ...code('cd ~/monitoring\ngit add evidence/screenshots\ngit commit -m "Add screenshots"\ngit push'),
);

// 12 daily use
add(
  h1("12. Daily use: stop and start"),
  table([
    ["Task", "Command"],
    ["Stop everything (data is kept)", "`./scripts/teardown.sh`"],
    ["Start again", "`./scripts/setup.sh`"],
    ["Health check", "`./scripts/verify.sh`"],
    ["See all pods", "`kubectl get pods -A`"],
    ["See host containers", "`docker compose ps`"],
    ["Logs of one service", "`docker logs prometheus` / `kubectl -n demo-app logs deploy/sample-app`"],
    ["Free memory", "`kubectl -n demo-app scale deploy/sample-app --replicas=1`"],
  ], [3600, 6038]),
);

// 13 troubleshooting
add(
  h1("13. Troubleshooting"),
  table([
    ["Problem", "Cause", "Fix"],
    ["`permission denied ... docker.sock`", "User not in docker group yet", "Log out and back in after Step 3.2"],
    ["minikube start fails: insufficient memory", "Too little free RAM", "Close programs, or use `--memory=2200`"],
    ["Prometheus target `sample-app` is DOWN", "minikube was re-created (new IP or token)", "Run `./scripts/setup.sh` again"],
    ["Target `cadvisor` or `kube-state-metrics` DOWN", "Pods still starting", "`kubectl get pods -n monitoring`; wait 1–2 min"],
    ["No logs in Grafana (Loki)", "Promtail cannot reach Loki", "`kubectl -n monitoring logs ds/promtail`; check `docker ps` shows loki"],
    ["Grafana: “Datasource provisioning error”", "Old Grafana volume with a different datasource", "`docker compose restart grafana`; if it persists: `docker volume rm monitoring_grafana_data`"],
    ["Port 3000/9090 already in use", "Another program uses the port", "Stop that program, or change the left-hand port in `docker-compose.yml`"],
    ["`ErrImageNeverPull` for sample-app", "Image not built inside minikube", "`minikube image build -t sample-app:1.1 k8s/app`"],
    ["Experiment run stopped halfway", "Computer slept or terminal closed", "`rm -rf experiments/data/runNN` and start `campaign.sh` again"],
  ], [3000, 2900, 3738]),
);

// 14 uninstall
add(
  h1("14. Remove everything"),
  ...code("cd ~/monitoring\ndocker compose down -v        # removes containers and stored data\nminikube delete                # removes the cluster"),
);

// ---------- document ----------
const doc = new Document({
  creator: "Chaitanya", title: "Setup and Installation Guide",
  styles: {
    default: { document: { run: { font: FONT, size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 32, bold: true, color: ACCENT },
        paragraph: { spacing: { before: 360, after: 160 }, keepNext: true, outlineLevel: 0,
          border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "BDD7EE", space: 4 } } } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 26, bold: true, color: "2E75B6" },
        paragraph: { spacing: { before: 240, after: 100 }, keepNext: true, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 22, bold: true, color: "404040" },
        paragraph: { spacing: { before: 180, after: 80 }, keepNext: true, outlineLevel: 2 } },
    ],
  },
  numbering: { config: numberingConfigs },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1134, bottom: 1134, left: 1134, right: 1134 } } },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [run("M.Tech Project Setup Guide · Page ", { size: 18, color: "808080" }),
        new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: "808080" })] })] }) },
    children: body,
  }],
});

Packer.toBuffer(doc).then((b) => { fs.writeFileSync("Setup-Guide.docx", b); console.log("written", b.length); });
