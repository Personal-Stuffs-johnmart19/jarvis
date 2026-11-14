import { t as translateMessage } from "./localization";
import React, { useEffect, useRef } from "react";
import { jarvisLogo as orb } from "./branding";
export function VoiceOrb({
  level,
  state,
  reduced = false,
}: {
  level: React.MutableRefObject<number>;
  state: string;
  reduced?: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const visual = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let id = 0,
      timer = 0,
      phase = 0,
      energy = 0,
      last = performance.now();
    const context = canvas.current?.getContext("2d");
    const frame = (now: number) => {
      const dt = Math.min(0.06, (now - last) / 1000);
      last = now;
      const target = level.current;
      energy +=
        (target - energy) * (1 - Math.exp(-(target > energy ? 18 : 7) * dt));
      if (!reduced && energy > 0.01) phase += dt * (0.4 + energy * 3);
      if (visual.current) {
        visual.current.style.setProperty("--energy", String(energy));
        visual.current.style.transform = reduced
          ? "none"
          : `scale(${1 + energy * 0.07}) rotate(${Math.sin(phase) * 2}deg)`;
      }
      if (context) {
        context.clearRect(0, 0, 400, 400);
        if (!reduced && energy > 0.01) {
          for (let n = 0; n < 64; n++) {
            const angle = n * 2.39996 + phase * (0.3 + (n % 7) / 10),
              radius = 125 + (n % 11) * 5 + Math.sin(phase + n) * energy * 14;
            context.beginPath();
            context.fillStyle = n % 3 ? "#b6ddff" : "#d4b5ff";
            context.globalAlpha = 0.25 + energy * 0.65;
            context.arc(
              200 + Math.cos(angle) * radius,
              200 + Math.sin(angle) * radius * 0.77,
              0.5 + energy * (1 + (n % 3)),
              0,
              Math.PI * 2,
            );
            context.fill();
          }
        }
      }
      if (energy > 0.005 || level.current > 0.005) {
        id = requestAnimationFrame(frame);
      } else {
        timer = window.setTimeout(() => {
          id = requestAnimationFrame(frame);
        }, 180);
      }
    };
    id = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(id);
      clearTimeout(timer);
    };
  }, [level, reduced]);
  return (
    <div
      className={"orb-scene " + (state === "THINKING" ? "thinking" : "")}
      aria-hidden="true"
    >
      <canvas ref={canvas} width="400" height="400" />
      <div className="orb-image" ref={visual}>
        <img src={orb} alt="" />
        <span>{translateMessage("JARVIS")}</span>
      </div>
    </div>
  );
}
