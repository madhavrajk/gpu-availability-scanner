# ☁️ GPU Availability Automation (GCP)

A Python-based system that dynamically scans Google Cloud regions and zones to detect **real GPU availability** and attempt VM provisioning using multiple validation strategies.

---

## 🚀 Overview

Cloud GPU availability is unpredictable — zones may:

* List GPUs as available ❌
* Pass quota checks ❌
* Still fail during allocation ❌

This project builds a **multi-step validation pipeline** to accurately determine GPU availability in real time.

---

## 🧠 Key Features

* 🔍 Scans all GCP zones for GPU availability (T4, L4)
* ⚡ Minimizes API calls via caching and batching
* 🧪 Uses **3 validation methods**:

  * **Method A**: GPU offered in zone (fast lookup)
  * **Method B**: Quota availability check
  * **Method C**: Real VM allocation (create + delete)
* 📊 Categorizes failure reasons (quota, capacity, policy, etc.)
* 📁 Outputs structured results to CSV

---

## 🧪 Validation Methods

### 🔹 Method A — Zone Capability

* Uses a single `accelerator-types list` API call
* Builds a zone → GPU lookup map
* Fast, but does **not guarantee availability**

---

### 🔹 Method B — Quota Check

* Uses:

  * `project-info describe` (global quota)
  * `regions describe` (regional quota)
* Cached per region to avoid redundant API calls
* Ensures allocation is allowed, but **not guaranteed**

---

### 🔹 Method C — Real Allocation (Most Accurate)

* Attempts actual VM creation with GPU
* Immediately deletes instance after success
* Detects real-time issues like:

  * Zone exhaustion
  * Policy restrictions
  * Billing errors

---

## 📊 Example Output

The script generates:

```id="gpu_csv"
gpu_results.csv
```

Containing:

* Zone + Region
* GPU availability status
* Allocation success/failure
* Failure reason
* Timing metrics

---

## 🔍 Failure Categorization

The system classifies failures into:

* 🚫 Quota exceeded
* ⚡ No GPUs available (zone exhausted)
* 🔒 Policy restrictions (e.g., external IP blocked)
* 💳 Billing / pricing issues
* 🔐 Permission errors
* ⏱️ Timeouts

---

## ⚡ Key Insights

* Passing quota checks ≠ real availability
* Many zones fail due to **resource exhaustion**
* Real allocation attempts are the only reliable signal
* API optimization significantly reduces runtime

---

## 🛠️ Tech Stack

* Python
* Google Cloud SDK (`gcloud`)
* Subprocess automation
* CSV logging

---

## ▶️ How to Run

### 1. Set your GCP project

```bash
gcloud config set project YOUR_PROJECT_ID
```

---

### 2. Run the script

```bash
python3 gpu_assign.py
```

---

## 📂 Project Structure

```id="gpu_structure"
.
├── gpu_assign.py        # Main script
├── gpu_results.csv      # Output results
├── README.md
```

---

## ⚠️ Notes

* Uses `--no-address` to avoid external IP policy issues
* Real allocation incurs minimal cost (VM is deleted immediately)
* Requires GCP CLI authentication

---

## 🔮 Future Improvements

* Parallel zone scanning (threading)
* Historical zone success prioritization
* AWS / multi-cloud support
* Dashboard for visualization

---

## 👨‍💻 Author

**Madhav Rajkondawar**
M.S. Machine Learning @ Columbia University

---

## ⭐ Star this repo if you found it useful!
