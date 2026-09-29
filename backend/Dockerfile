# UNTESTED: image tag and pip step not verified here (no Docker in the authoring sandbox).
FROM opencfd/openfoam-default:2312
USER root
RUN apt-get update && apt-get install -y python3 python3-pip libglu1-mesa libxrender1 libxcursor1 libxft2 libxinerama1 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --break-system-packages gmsh numpy
COPY server.py /app/server.py
EXPOSE 8000
# OpenFOAM env must be sourced so simpleFoam/snappyHexMesh are on PATH
CMD ["bash","-lc","source /usr/lib/openfoam/openfoam2312/etc/bashrc && python3 /app/server.py"]
