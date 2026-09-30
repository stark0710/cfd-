import json, math, os, subprocess, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(__file__)); import pipeline as P
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
class T(unittest.TestCase):
    @classmethod
    def setUpClass(c):
        c.d = tempfile.mkdtemp(); c.step = os.path.join(c.d, "w.step")
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_test_wing.py"), c.step], check=True, capture_output=True)
    def test_isa(self):
        a = P.isa(0); self.assertAlmostEqual(a["rho"], 1.225, 3); self.assertAlmostEqual(a["T"], 288.15)
    def test_wind(self):
        U, d, l = P.wind(25, 4, 0); self.assertAlmostEqual(math.hypot(*U), 25); self.assertAlmostEqual(sum(x*y for x, y in zip(d, l)), 0)
        self.assertGreater(U[2], 0); self.assertGreater(l[2], 0)
    def test_geometry(self):
        stl = os.path.join(self.d, "a.stl"); i = P.geometry_to_stl(self.step, stl)
        self.assertEqual(i["solids"], 1); self.assertEqual(i["open_edges"], 0); self.assertAlmostEqual(i["length_scale_m"], 1.0, 2)
        self.assertGreater(i["triangles"], 500); self.assertIn("solid aircraft", open(stl).read(200))
        case = os.path.join(self.d, "case"); atm = P.isa(0); m = P.write_case(case, i, atm)
        for f in ["system/blockMeshDict", "system/snappyHexMeshDict", "system/controlDict", "system/fvSchemes", "system/fvSolution", "constant/transportProperties", "constant/turbulenceProperties", "0/U", "0/p", "0/k", "0/omega", "0/nut"]:
            t = open(os.path.join(case, f)).read(); self.assertEqual(t.count("{"), t.count("}"), f); self.assertNotIn("$", t, f)
        self.assertEqual(m["levels"], "5 5"); self.assertAlmostEqual(m["chord_proxy_m"], 0.2, 2)
        x, y, z = m["location_in_mesh"]; b = i["bbox_m"]; self.assertLess(x, b[0]); self.assertGreater(x, m["domain_m"][0])
    def test_bad_step(self):
        p = os.path.join(self.d, "bad.step"); open(p, "w").write("ISO-10303-21;\nEND-ISO-10303-21;\n")
        with self.assertRaises(P.PipelineError): P.geometry_to_stl(p, os.path.join(self.d, "b.stl"))
    def test_parsers(self):
        ev = []; r = P.ResidualParser(lambda **e: ev.append(e))
        for l in ["Time = 1", "smoothSolver:  Solving for Ux, Initial residual = 0.5, Final residual = 0.01, No Iterations 2", "GAMG:  Solving for p, Initial residual = 0.9, Final residual = 0.05, No Iterations 3", "Time = 2", "SIMPLE solution converged in 2 iterations"]: r.feed(l)
        r.flush(); self.assertEqual(ev[0]["vals"], {"Ux": 0.5, "p": 0.9}); self.assertTrue(r.converged)
        m = P.parse_checkmesh("    cells:            1234\n    Mesh non-orthogonality Max: 40.5 average: 5\n    Max skewness = 0.8\nMesh OK.\n"); self.assertTrue(m["mesh_ok"]); self.assertEqual(m["cells"], 1234); self.assertEqual(m["max_non_orthogonality"], 40.5)
        y = P.parse_yplus("patch aircraft_aircraft y+ : min = 10.5, max = 400, average = 120.25"); self.assertEqual(y["avg"], 120.25)
    def test_forces(self):
        d = os.path.join(self.d, "fc", "postProcessing", "forces", "0"); os.makedirs(d)
        open(os.path.join(d, "force.dat"), "w").write("# Time (total_x total_y total_z) (pressure_x pressure_y pressure_z) (viscous_x viscous_y viscous_z)\n" + "".join(f"{i} ((1.0 0.0 10.0) (0.9 0 9.9) (0.1 0 0.1))\n" for i in range(1, 30)))
        rows = P.parse_forces(os.path.join(self.d, "fc")); s = P.force_summary(rows, (1, 0, 0), (0, 0, 1)); self.assertAlmostEqual(s["lift_N"], 10.0); self.assertAlmostEqual(s["lift_to_drag"], 10.0); self.assertLess(s["lift_drift"], 1e-9)
    @unittest.skipIf(__import__("shutil").which("docker"), "docker present: this test needs docker to be absent")
    def test_pipeline_without_docker(self):
        ev = []; r = P.run_pipeline(self.step, os.path.join(self.d, "run"), lambda **e: ev.append(e))
        self.assertEqual(r["status"], "failed"); self.assertIn("tool_missing", json.dumps(r)); self.assertIn("error", [e["type"] for e in ev]); self.assertIn("FAILED", r and open(os.path.join(self.d, "run", "report.md")).read())
if __name__ == "__main__": unittest.main(verbosity=2)
