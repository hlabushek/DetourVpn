import React from "react";

/** Decorative Soft Orbit glass blob (raster artwork chosen by theme in CSS);
    never represents a measurement. */
export function Orbit({
    tone = "idle",
    children,
}: {
    tone?: "idle" | "error";
    children?: React.ReactNode;
}) {
    return (
        <div className={`orbit-blob ${tone}`}>
            <span className="orbit-art" aria-hidden="true" />
            <div className="orbit-content">{children}</div>
        </div>
    );
}
