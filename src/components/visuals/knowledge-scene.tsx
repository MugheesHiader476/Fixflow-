"use client";

import { Canvas } from "@react-three/fiber";

// Static, on-demand scene. No model files, textures, remote assets or animation loop.
export default function KnowledgeScene({ onUnavailable }: { onUnavailable: () => void }) {
  return <Canvas orthographic camera={{ position: [5, 4, 7], zoom: 40 }} dpr={[1, 1.5]} frameloop="demand"
    gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
    onCreated={({ gl }) => { gl.domElement.addEventListener("webglcontextlost", onUnavailable, { once: true }); }}>
    <ambientLight intensity={2} />
    <directionalLight position={[3, 5, 4]} intensity={4} color="#fff1df" />
    <directionalLight position={[-3, 1, -2]} intensity={2} color="#87b8a1" />
    <group rotation={[0, 0.2, -0.12]}>
      {[-0.55, 0, 0.55].map((y, index) => <mesh key={y} position={[index * 0.12, y, 0]} rotation={[0, index * 0.12, 0]}>
        <boxGeometry args={[2.15, 0.16, 1.45]} />
        <meshStandardMaterial color={["#9aafa0", "#d5d8bd", "#e9dfc8"][index]} roughness={0.35} metalness={0.3} />
      </mesh>)}
      <mesh position={[0.9, 0.3, 0.9]} rotation={[0.4, 0.2, 0.15]}>
        <torusGeometry args={[0.48, 0.07, 12, 48]} />
        <meshStandardMaterial color="#cb8869" roughness={0.25} metalness={0.6} />
      </mesh>
    </group>
  </Canvas>;
}
