#!/usr/bin/env python3
"""Generate benchmark charts from results."""

import json
from pathlib import Path
import matplotlib.pyplot as plt
import numpy as np

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

# Data from benchmark runs (including manually recorded failed runs)
RESULTS = {
    "task1_understand": {
        "name": "Understand Function",
        "without": {"tokens": 602625, "iterations": 39, "completed": True, "time_sec": 19*60},
        "with": {"tokens": 130627, "iterations": 13, "completed": True, "time_sec": 5*60},
    },
    "task2_find_usages": {
        "name": "Find All Usages",
        "without": {"tokens": 622631, "iterations": 34, "completed": False, "time_sec": 19*60+40},
        "with": {"tokens": 136721, "iterations": 12, "completed": True, "time_sec": 4*60+17},
    },
    "task3_safe_change": {
        "name": "Safe Code Change",
        "without": {"tokens": 50000, "iterations": 11, "completed": False, "time_sec": 39, "error": "Rate Limit"},
        "with": {"tokens": 100000, "iterations": 13, "completed": True, "time_sec": 2*60+57},
    },
}

# Read actual token counts from saved files
for task_id in RESULTS:
    with_path = RESULTS_DIR / f"{task_id}_with_lenspr.json"
    if with_path.exists():
        with open(with_path) as f:
            data = json.load(f)
            RESULTS[task_id]["with"]["tokens"] = data["total_input_tokens"]
            RESULTS[task_id]["with"]["iterations"] = data["iterations"]

# Colors
COLOR_WITHOUT = '#ff6b6b'
COLOR_WITH = '#4ecdc4'

# ============================================
# Chart 1: Iterations Comparison
# ============================================
fig, ax = plt.subplots(figsize=(10, 6))

tasks = list(RESULTS.keys())
task_names = [RESULTS[t]["name"] for t in tasks]
x = np.arange(len(tasks))
width = 0.35

without_iters = [RESULTS[t]["without"]["iterations"] for t in tasks]
with_iters = [RESULTS[t]["with"]["iterations"] for t in tasks]
without_completed = [RESULTS[t]["without"]["completed"] for t in tasks]
with_completed = [RESULTS[t]["with"]["completed"] for t in tasks]

bars1 = ax.bar(x - width/2, without_iters, width, label='Without LensPR', color=COLOR_WITHOUT)
bars2 = ax.bar(x + width/2, with_iters, width, label='With LensPR', color=COLOR_WITH)

# Add completion markers
for i, (completed, bar) in enumerate(zip(without_completed, bars1)):
    marker = '✓' if completed else '✗'
    color = 'green' if completed else 'red'
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, marker,
            ha='center', va='bottom', fontsize=16, fontweight='bold', color=color)

for i, (completed, bar) in enumerate(zip(with_completed, bars2)):
    marker = '✓' if completed else '✗'
    color = 'green' if completed else 'red'
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, marker,
            ha='center', va='bottom', fontsize=16, fontweight='bold', color=color)

ax.set_ylabel('Iterations', fontsize=12)
ax.set_title('Iterations to Complete Task\n(✓ = completed, ✗ = failed/rate limit)', fontsize=14)
ax.set_xticks(x)
ax.set_xticklabels(task_names, fontsize=11)
ax.legend(fontsize=11)
ax.set_ylim(0, max(without_iters) * 1.2)

plt.tight_layout()
plt.savefig(RESULTS_DIR / 'chart_iterations.png', dpi=150, bbox_inches='tight')
print(f"Saved: {RESULTS_DIR / 'chart_iterations.png'}")

# ============================================
# Chart 2: Token Usage Comparison
# ============================================
fig, ax = plt.subplots(figsize=(10, 6))

without_tokens = [RESULTS[t]["without"]["tokens"]/1000 for t in tasks]
with_tokens = [RESULTS[t]["with"]["tokens"]/1000 for t in tasks]

bars1 = ax.bar(x - width/2, without_tokens, width, label='Without LensPR', color=COLOR_WITHOUT)
bars2 = ax.bar(x + width/2, with_tokens, width, label='With LensPR', color=COLOR_WITH)

# Add value labels
for bar, val in zip(bars1, without_tokens):
    label = f'{val:.0f}k'
    if not RESULTS[tasks[bars1.index(bar)]]["without"]["completed"]:
        label += '*'
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 10, label,
            ha='center', va='bottom', fontsize=10)

for bar, val in zip(bars2, with_tokens):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 10, f'{val:.0f}k',
            ha='center', va='bottom', fontsize=10)

ax.set_ylabel('Input Tokens (thousands)', fontsize=12)
ax.set_title('Total Context Tokens Consumed\n(* = task failed before completion)', fontsize=14)
ax.set_xticks(x)
ax.set_xticklabels(task_names, fontsize=11)
ax.legend(fontsize=11)

plt.tight_layout()
plt.savefig(RESULTS_DIR / 'chart_tokens.png', dpi=150, bbox_inches='tight')
print(f"Saved: {RESULTS_DIR / 'chart_tokens.png'}")

# ============================================
# Chart 3: Success Rate Comparison
# ============================================
fig, ax = plt.subplots(figsize=(8, 6))

without_success = sum(1 for t in tasks if RESULTS[t]["without"]["completed"])
with_success = sum(1 for t in tasks if RESULTS[t]["with"]["completed"])

categories = ['Without LensPR', 'With LensPR']
success = [without_success, with_success]
total = len(tasks)

bars = ax.bar(categories, success, color=[COLOR_WITHOUT, COLOR_WITH], edgecolor='black', linewidth=2)

# Add labels
for bar, val in zip(bars, success):
    pct = val / total * 100
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
            f'{val}/{total}\n({pct:.0f}%)', ha='center', va='bottom', fontsize=14, fontweight='bold')

ax.set_ylabel('Tasks Completed Successfully', fontsize=12)
ax.set_title('Task Completion Rate', fontsize=14)
ax.set_ylim(0, total + 1)
ax.axhline(y=total, color='gray', linestyle='--', alpha=0.5)

plt.tight_layout()
plt.savefig(RESULTS_DIR / 'chart_success.png', dpi=150, bbox_inches='tight')
print(f"Saved: {RESULTS_DIR / 'chart_success.png'}")

# ============================================
# Chart 4: Summary Comparison
# ============================================
fig, axes = plt.subplots(1, 3, figsize=(14, 5))

# Tokens
ax = axes[0]
total_without = sum(RESULTS[t]["without"]["tokens"] for t in tasks) / 1000
total_with = sum(RESULTS[t]["with"]["tokens"] for t in tasks) / 1000
bars = ax.bar(['Without', 'With'], [total_without, total_with], color=[COLOR_WITHOUT, COLOR_WITH])
ax.set_ylabel('Total Tokens (K)')
ax.set_title('Total Tokens Consumed')
for bar, val in zip(bars, [total_without, total_with]):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 20, f'{val:.0f}K',
            ha='center', fontsize=12, fontweight='bold')
savings = (total_without - total_with) / total_without * 100
ax.text(0.5, 0.95, f'↓ {savings:.0f}% savings', transform=ax.transAxes, ha='center',
        fontsize=11, color='green', fontweight='bold')

# Iterations
ax = axes[1]
total_without = sum(RESULTS[t]["without"]["iterations"] for t in tasks)
total_with = sum(RESULTS[t]["with"]["iterations"] for t in tasks)
bars = ax.bar(['Without', 'With'], [total_without, total_with], color=[COLOR_WITHOUT, COLOR_WITH])
ax.set_ylabel('Total Iterations')
ax.set_title('Total API Calls')
for bar, val in zip(bars, [total_without, total_with]):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1, str(val),
            ha='center', fontsize=12, fontweight='bold')
savings = (total_without - total_with) / total_without * 100
ax.text(0.5, 0.95, f'↓ {savings:.0f}% fewer', transform=ax.transAxes, ha='center',
        fontsize=11, color='green', fontweight='bold')

# Success Rate
ax = axes[2]
bars = ax.bar(['Without', 'With'], [without_success, with_success], color=[COLOR_WITHOUT, COLOR_WITH])
ax.set_ylabel('Tasks Completed')
ax.set_title('Success Rate')
ax.set_ylim(0, 4)
for bar, val in zip(bars, [without_success, with_success]):
    pct = val / total * 100
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1, f'{val}/3\n({pct:.0f}%)',
            ha='center', fontsize=12, fontweight='bold')

plt.suptitle('LensPR Benchmark Summary', fontsize=16, fontweight='bold', y=1.02)
plt.tight_layout()
plt.savefig(RESULTS_DIR / 'chart_summary.png', dpi=150, bbox_inches='tight')
print(f"Saved: {RESULTS_DIR / 'chart_summary.png'}")

# ============================================
# Print Summary Table
# ============================================
print("\n" + "="*80)
print("BENCHMARK RESULTS SUMMARY")
print("="*80)
print(f"\n{'Task':<25} {'Mode':<10} {'Tokens':>12} {'Iterations':>12} {'Status':>10}")
print("-"*80)

for task_id in tasks:
    task = RESULTS[task_id]
    for mode in ['without', 'with']:
        data = task[mode]
        mode_label = 'WITHOUT' if mode == 'without' else 'WITH'
        status = '✓' if data['completed'] else '✗ ' + data.get('error', 'Failed')
        print(f"{task['name']:<25} {mode_label:<10} {data['tokens']:>12,} {data['iterations']:>12} {status:>10}")
    print()

# Totals
total_without_tokens = sum(RESULTS[t]["without"]["tokens"] for t in tasks)
total_with_tokens = sum(RESULTS[t]["with"]["tokens"] for t in tasks)
total_without_iter = sum(RESULTS[t]["without"]["iterations"] for t in tasks)
total_with_iter = sum(RESULTS[t]["with"]["iterations"] for t in tasks)

print("-"*80)
print(f"{'TOTAL':<25} {'WITHOUT':<10} {total_without_tokens:>12,} {total_without_iter:>12} {without_success}/3")
print(f"{'TOTAL':<25} {'WITH':<10} {total_with_tokens:>12,} {total_with_iter:>12} {with_success}/3")
print()
print(f"Token savings: {(total_without_tokens - total_with_tokens) / total_without_tokens * 100:.1f}%")
print(f"Iteration savings: {(total_without_iter - total_with_iter) / total_without_iter * 100:.1f}%")
print(f"Success rate improvement: {without_success}/3 → {with_success}/3")
print("="*80)
