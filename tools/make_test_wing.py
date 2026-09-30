"""Make a simple NACA0012 rectangular wing STEP (chord 200 mm, span 1000 mm, nose at -X, +Y starboard, +Z up).
Usage: python tools/make_test_wing.py test_wing.step   (units in the file: mm)"""
import sys, math, gmsh
out = sys.argv[1] if len(sys.argv) > 1 else "test_wing.step"
c, span, t = 200.0, 1000.0, 0.12
def yt(x): return 5*t*(0.2969*math.sqrt(x)-0.1260*x-0.3516*x**2+0.2843*x**3-0.1036*x**4)  # closed trailing edge
xs = [(1-math.cos(math.pi*i/40))/2 for i in range(41)]   # LE -> TE, cosine spacing
gmsh.initialize(); gmsh.option.setNumber("General.Terminal", 0); gmsh.model.add("wing")
o = gmsh.model.occ
def P(x, y): return o.addPoint(x*c, 0.0, y*c)
up = [P(x, yt(x)) for x in xs]
lo = [up[0]] + [P(x, -yt(x)) for x in xs[1:-1]] + [up[-1]]
s1 = o.addSpline(up); s2 = o.addSpline(lo[::-1])
face = o.addPlaneSurface([o.addCurveLoop([s1, s2])])
o.extrude([(2, face)], 0, span, 0)
o.translate(o.getEntities(3), 0, -span/2, 0)
o.synchronize(); gmsh.write(out); gmsh.finalize(); print("wrote", out)
