import { createElement, type CSSProperties } from "react";
import type { ImageProps } from "next/image";
export default function Image({ src, alt, fill, preload, ...props }: ImageProps) {
  const style: CSSProperties = fill ? { position: "absolute", inset: 0, width: "100%", height: "100%", ...props.style } : props.style ?? {};
  return createElement("img", { ...props, src: typeof src === "string" ? src : "", alt, style, loading: preload ? "eager" : "lazy" });
}
