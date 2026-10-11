"""Run an automated angle-of-attack polar sweep:
Usage: python backend/run_polar.py test_plane.step --alphas 0 2 4 --out runs/plane-polar
"""
import argparse, csv, json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
import pipeline
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None

def main():
    parser = argparse.ArgumentParser(description="Automated CFD angle-of-attack polar sweep")
    parser.add_argument("step", help="Path to input STEP file")
    parser.add_argument("--alphas", nargs="+", type=float, default=[0.0, 2.0, 4.0], help="List of angles of attack in degrees")
    parser.add_argument("--out", default="runs/polar", help="Output directory for polar sweep")
    parser.add_argument("--wake-box", type=int, default=0, help="Enable wake box (1) or disable (0)")
    parser.add_argument("--mesh-levels", default="5 7", help="snappyHexMesh levels")
    parser.add_argument("--reuse-mesh", dest="reuse_mesh", action="store_true", default=True, help="Reuse mesh from the first angle (default: True)")
    parser.add_argument("--no-reuse-mesh", dest="reuse_mesh", action="store_false", help="Disable mesh reuse")
    parser.add_argument("--warm-start", dest="warm_start", action="store_true", default=True, help="Warm-start flow fields from previous angle (default: True)")
    parser.add_argument("--no-warm-start", dest="warm_start", action="store_false", help="Disable warm-starting flow fields")
    parser.add_argument("--initial-case", help="Path to existing case to reuse mesh and warm-start from")
    args = parser.parse_args()

    step_path = os.path.abspath(args.step)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    os.environ["WAKE_BOX"] = str(args.wake_box)
    os.environ["MESH_LEVELS"] = args.mesh_levels
    pipeline.WAKE_BOX = bool(args.wake_box)
    pipeline.MESH_LEVELS = args.mesh_levels

    results = []
    first_run_dir = os.path.abspath(args.initial_case) if args.initial_case else None
    last_completed_dir = os.path.abspath(args.initial_case) if args.initial_case else None
    print(f"=== Starting Polar Sweep on {os.path.basename(step_path)} ===")
    print(f"Angles of attack: {args.alphas}")
    print(f"Reuse mesh across angles: {args.reuse_mesh}")
    print(f"Warm-start flow fields: {args.warm_start}")
    if args.initial_case: print(f"Initial seed case: {args.initial_case}")
    print(f"Output directory: {out_dir}\n")


    for a in args.alphas:
        run_name = f"alpha_{a:g}".replace(".", "_")
        run_dir = os.path.join(out_dir, run_name)
        os.makedirs(run_dir, exist_ok=True)
        os.environ["CFD_ALPHA"] = str(a)
        pipeline.CASE["alpha"] = float(a)

        mesh_source = first_run_dir if args.reuse_mesh else None
        warm_source = last_completed_dir if args.warm_start else None

        print(f"--- Running Alpha = {a:g} deg ---")
        t0 = time.time()
        def emit(**e):
            if e["type"] == "stage": print(f"  Stage: {pipeline.STAGES[e['i']]}")
            elif e["type"] == "log": print(f"    {e['m']}")
            elif e["type"] == "error": print(f"    ERROR [{e['code']}]: {e['m']}")

        res = pipeline.run_pipeline(step_path, run_dir, emit, mesh_source_case=mesh_source, warm_start_case=warm_source)
        if res.get("status") in ["completed", "completed_checks_passed", "completed_with_warnings"]:
            if first_run_dir is None: first_run_dir = run_dir
            last_completed_dir = run_dir


        dt = time.time() - t0
        fs = res.get("forces") or {}
        ref = res.get("reference") or {}
        row = {
            "alpha_deg": a,
            "lift_N": fs.get("lift_N"),
            "drag_N": fs.get("drag_N"),
            "CL": fs.get("CL"),
            "CD": fs.get("CD"),
            "L_over_D": fs.get("lift_to_drag"),
            "iterations": res.get("iterations"),
            "wall_time_s": round(dt, 1),
            "status": res.get("status")
        }
        results.append(row)
        print(f"  Done in {dt:.1f}s | Lift: {row['lift_N']} N, Drag: {row['drag_N']} N, CL: {row['CL']}, CD: {row['CD']}\n")

    # Write CSV
    csv_path = os.path.join(out_dir, "polar.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"Saved polar data to {csv_path}")

    # Generate Plots if matplotlib is available
    if plt and len(results) > 1:
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
        alphas = [r["alpha_deg"] for r in results]
        cls = [r["CL"] or 0 for r in results]
        cds = [r["CD"] or 0 for r in results]
        lds = [r["L_over_D"] or 0 for r in results]

        # 1. CL vs Alpha
        axes[0].plot(alphas, cls, "o-b", lw=2, ms=6)
        axes[0].set_title("Lift Coefficient vs AoA")
        axes[0].set_xlabel("Angle of Attack (deg)")
        axes[0].set_ylabel("CL")
        axes[0].grid(True, alpha=0.3)

        # 2. CD vs Alpha
        axes[1].plot(alphas, cds, "s-r", lw=2, ms=6)
        axes[1].set_title("Drag Coefficient vs AoA")
        axes[1].set_xlabel("Angle of Attack (deg)")
        axes[1].set_ylabel("CD")
        axes[1].grid(True, alpha=0.3)

        # 3. Drag Polar (CL vs CD)
        axes[2].plot(cds, cls, "^-g", lw=2, ms=6)
        axes[2].set_title("Drag Polar (CL vs CD)")
        axes[2].set_xlabel("CD")
        axes[2].set_ylabel("CL")
        axes[2].grid(True, alpha=0.3)

        plt.tight_layout()
        plot_path = os.path.join(out_dir, "polar.png")
        plt.savefig(plot_path, dpi=150)
        plt.close()
        print(f"Saved polar curves to {plot_path}")

    # Print summary table
    print("\n=== POLAR SUMMARY TABLE ===")
    print(f"{'Alpha (deg)':<12}{'Lift (N)':<12}{'Drag (N)':<12}{'CL':<10}{'CD':<10}{'L/D':<10}{'Status':<25}")
    for r in results:
        l = f"{r['lift_N']:.2f}" if r['lift_N'] is not None else "n/a"
        d = f"{r['drag_N']:.2f}" if r['drag_N'] is not None else "n/a"
        cl = f"{r['CL']:.4f}" if r['CL'] is not None else "n/a"
        cd = f"{r['CD']:.4f}" if r['CD'] is not None else "n/a"
        ld = f"{r['L_over_D']:.2f}" if r['L_over_D'] is not None else "n/a"
        print(f"{r['alpha_deg']:<12.1f}{l:<12}{d:<12}{cl:<10}{cd:<10}{ld:<10}{r['status']:<25}")

if __name__ == "__main__":
    main()
