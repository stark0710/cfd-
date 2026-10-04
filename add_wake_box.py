import sys
p = sys.argv[1]; s = open(p).read()
def rep(a, b):
    global s
    assert s.count(a) == 1, "pattern not found exactly once: " + a[:60]
    s = s.replace(a, b)
rep('END_TIME = int(os.environ.get("END_TIME", "600"))',
    'END_TIME = int(os.environ.get("END_TIME", "600"))\nWAKE_BOX = os.environ.get("WAKE_BOX", "0") == "1"\nWAKE_LEVEL = os.environ.get("WAKE_LEVEL")')
rep('    nu = atm["nu"]; Rex = V*chord/2/nu;',
'''    wb = None; gbox = rreg = ""
    if WAKE_BOX:
        wl = int(WAKE_LEVEL) if WAKE_LEVEL else max(2, int(levels.split()[0]) - 1)
        zc = (z0+z1)/2; bmin = (x0-0.25*chord, y0-0.5*chord, zc-0.75*chord); bmax = (x1+3*chord, y1+0.5*chord, zc+0.75*chord)
        gbox = "wakebox { type searchableBox; min (%g %g %g); max (%g %g %g); }" % (bmin + bmax)
        rreg = "wakebox { mode inside; levels ((1e15 %d)); }" % wl
        wb = dict(level=wl, min_m=bmin, max_m=bmax, cell_m=h/2**wl)
    nu = atm["nu"]; Rex = V*chord/2/nu;''')
rep('geometry { aircraft.stl { type triSurfaceMesh; name aircraft; } }', 'geometry { aircraft.stl { type triSurfaceMesh; name aircraft; } $gbox }')
rep('refinementRegions {} locationInMesh', 'refinementRegions { $rreg } locationInMesh')
rep('lv=levels, rfa=RESOLVE_ANGLE,', 'lv=levels, gbox=gbox, rreg=rreg, rfa=RESOLVE_ANGLE,')
rep('return dict(layers=lay, levels=levels,', 'return dict(wake_box=wb, layers=lay, levels=levels,')
open(p, "w").write(s); print("patched", p)
