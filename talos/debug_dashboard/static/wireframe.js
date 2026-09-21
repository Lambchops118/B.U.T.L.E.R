"use strict";

(() => {
  const canvas = document.getElementById("wireframe-canvas");
  const panel = document.getElementById("panel-wireframe");
  if (!canvas || !panel) return;

  const vertices = [];
  const edges = [];
  const addVertex = (x, y, z) => { vertices.push([x, y, z]); return vertices.length - 1; };
  const addEdge = (a, b) => edges.push([a, b]);

  function addRing(y, radius, sides = 8, phase = Math.PI / 8) {
    return Array.from({ length: sides }, (_, index) => {
      const angle = phase + index * Math.PI * 2 / sides;
      return addVertex(Math.cos(angle) * radius, y, Math.sin(angle) * radius);
    });
  }

  function closeRing(ring) {
    ring.forEach((vertex, index) => addEdge(vertex, ring[(index + 1) % ring.length]));
  }

  function joinRings(a, b, diagonals = false) {
    a.forEach((vertex, index) => {
      addEdge(vertex, b[index]);
      if (diagonals) addEdge(vertex, b[(index + 1) % b.length]);
    });
  }

  function addDish(cx, cy, cz) {
    const rings = [0.14, 0.3, 0.48].map((radius, ringIndex) => {
      const y = cy - ringIndex * 0.07;
      const ring = Array.from({ length: 12 }, (_, index) => {
        const angle = index * Math.PI / 6;
        return addVertex(cx + Math.cos(angle) * radius, y, cz + Math.sin(angle) * radius);
      });
      closeRing(ring);
      return ring;
    });
    for (let index = 0; index < 12; index += 2) {
      addEdge(rings[0][index], rings[1][index]);
      addEdge(rings[1][index], rings[2][index]);
    }
    const receiver = addVertex(cx, cy + 0.28, cz);
    [0, 3, 6, 9].forEach((index) => addEdge(receiver, rings[2][index]));
    return receiver;
  }

  function buildLunarModule() {
    const descentBottom = addRing(-0.72, 1.03);
    const descentTop = addRing(0.02, 1.16);
    closeRing(descentBottom); closeRing(descentTop); joinRings(descentBottom, descentTop, true);

    const cabinBottom = addRing(0.02, 0.78);
    const cabinShoulder = addRing(0.86, 0.82);
    const cabinTop = addRing(1.38, 0.47);
    closeRing(cabinBottom); closeRing(cabinShoulder); closeRing(cabinTop);
    joinRings(cabinBottom, cabinShoulder, true); joinRings(cabinShoulder, cabinTop, true);

    const roof = addVertex(0, 1.58, 0);
    cabinTop.forEach((vertex) => addEdge(vertex, roof));

    // Four landing legs, paired braces, and octagonal footpads.
    [Math.PI / 4, Math.PI * 3 / 4, Math.PI * 5 / 4, Math.PI * 7 / 4].forEach((angle, legIndex) => {
      const hip = descentTop[(legIndex * 2) % 8];
      const knee = addVertex(Math.cos(angle) * 1.62, -0.72, Math.sin(angle) * 1.62);
      const foot = addVertex(Math.cos(angle) * 2.05, -1.34, Math.sin(angle) * 2.05);
      addEdge(hip, knee); addEdge(knee, foot);
      addEdge(descentBottom[(legIndex * 2 + 1) % 8], knee);
      addEdge(descentTop[(legIndex * 2 + 7) % 8], knee);
      const pad = Array.from({ length: 8 }, (_, index) => {
        const a = index * Math.PI / 4;
        return addVertex(
          Math.cos(angle) * 2.05 + Math.cos(a) * 0.28,
          -1.36,
          Math.sin(angle) * 2.05 + Math.sin(a) * 0.28,
        );
      });
      closeRing(pad); addEdge(foot, pad[0]); addEdge(foot, pad[4]);
    });

    // Forward ladder and triangular porch structure.
    const ladderLeft = [];
    const ladderRight = [];
    for (let rung = 0; rung < 7; rung += 1) {
      const y = 0.05 - rung * 0.19;
      ladderLeft.push(addVertex(-0.17, y, 1.12 + rung * 0.055));
      ladderRight.push(addVertex(0.17, y, 1.12 + rung * 0.055));
      addEdge(ladderLeft[rung], ladderRight[rung]);
      if (rung) { addEdge(ladderLeft[rung - 1], ladderLeft[rung]); addEdge(ladderRight[rung - 1], ladderRight[rung]); }
    }
    addEdge(cabinShoulder[1], ladderLeft[0]); addEdge(cabinShoulder[2], ladderRight[0]);

    // Mast and shallow parabolic rendezvous dish.
    const mastBase = addVertex(0.25, 1.48, -0.2);
    const mastTop = addVertex(0.25, 2.08, -0.2);
    addEdge(roof, mastBase); addEdge(mastBase, mastTop);
    const receiver = addDish(0.25, 2.13, -0.2);
    addEdge(mastTop, receiver);

    // Four small reaction-control cages around the ascent stage.
    [0, Math.PI / 2, Math.PI, Math.PI * 3 / 2].forEach((angle) => {
      const tangentX = -Math.sin(angle) * 0.14;
      const tangentZ = Math.cos(angle) * 0.14;
      const centerX = Math.cos(angle) * 1.02;
      const centerZ = Math.sin(angle) * 1.02;
      const lowerA = addVertex(centerX + tangentX, 0.54, centerZ + tangentZ);
      const lowerB = addVertex(centerX - tangentX, 0.54, centerZ - tangentZ);
      const upperA = addVertex(centerX + tangentX, 0.91, centerZ + tangentZ);
      const upperB = addVertex(centerX - tangentX, 0.91, centerZ - tangentZ);
      addEdge(lowerA, lowerB); addEdge(lowerB, upperB); addEdge(upperB, upperA); addEdge(upperA, lowerA);
      addEdge(lowerA, upperB); addEdge(lowerB, upperA);
    });
  }

  buildLunarModule();

  const context = canvas.getContext("2d");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let angleY = -0.55;
  let angleX = -0.14;
  let lastTime = performance.now();
  let dragging = false;
  let pointerX = 0;
  let pointerY = 0;

  function resize() {
    const rect = canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    return { width, height, ratio };
  }

  function transform([x, y, z]) {
    const cy = Math.cos(angleY); const sy = Math.sin(angleY);
    const cx = Math.cos(angleX); const sx = Math.sin(angleX);
    const rotatedX = x * cy + z * sy;
    const rotatedZ = -x * sy + z * cy;
    return [rotatedX, y * cx - rotatedZ * sx, y * sx + rotatedZ * cx];
  }

  function render(now) {
    const size = resize();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.fillStyle = "#000";
    context.fillRect(0, 0, size.width, size.height);

    if (!panel.hidden) {
      const elapsed = Math.min(now - lastTime, 50);
      if (!reduceMotion && !dragging) angleY += elapsed * 0.00012;
      const scale = Math.min(size.width, size.height) * 0.17;
      const centerX = size.width * 0.5;
      const centerY = size.height * 0.54;
      const projected = vertices.map((vertex) => {
        const [x, y, z] = transform(vertex);
        const perspective = 5.8 / (6.5 + z);
        return [centerX + x * scale * perspective, centerY - y * scale * perspective, z];
      });

      const orderedEdges = edges.slice().sort((a, b) => {
        const az = projected[a[0]][2] + projected[a[1]][2];
        const bz = projected[b[0]][2] + projected[b[1]][2];
        return az - bz;
      });
      context.lineWidth = Math.max(0.7 * size.ratio, 1);
      context.strokeStyle = "#63ed91";
      context.globalAlpha = 0.82;
      context.beginPath();
      orderedEdges.forEach(([start, end]) => {
        context.moveTo(projected[start][0], projected[start][1]);
        context.lineTo(projected[end][0], projected[end][1]);
      });
      context.stroke();
      context.globalAlpha = 1;
    }
    lastTime = now;
    requestAnimationFrame(render);
  }

  canvas.addEventListener("pointerdown", (event) => {
    dragging = true; pointerX = event.clientX; pointerY = event.clientY;
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    angleY += (event.clientX - pointerX) * 0.008;
    angleX = Math.max(-1.05, Math.min(1.05, angleX + (event.clientY - pointerY) * 0.006));
    pointerX = event.clientX; pointerY = event.clientY;
  });
  canvas.addEventListener("pointerup", (event) => { dragging = false; canvas.releasePointerCapture(event.pointerId); });
  canvas.addEventListener("pointercancel", () => { dragging = false; });
  requestAnimationFrame(render);
})();
