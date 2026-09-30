#!/usr/bin/env bash
# Prepares a Ubuntu Codespace/VM: Gmsh (python) + OpenFOAM image. Requires Docker.
set -e
sudo apt-get update -q && sudo apt-get install -y -q libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 libgl1
pip install --user gmsh numpy
docker pull opencfd/openfoam-default:2312
python3 -c "import gmsh; print('gmsh', gmsh.__version__)"
echo "OK. Next: python tools/make_test_wing.py test_wing.step && python backend/run_local.py test_wing.step"
