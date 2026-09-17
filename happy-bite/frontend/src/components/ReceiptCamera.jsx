/* Receipt camera — a live view, a shutter, and a look at the photo before it
   is read.

   `<input type="file" capture>` only opens a camera on phones. On a laptop
   Chrome ignores `capture` and shows a file picker, so this asks for the camera
   itself and hands back a JPEG File — the same thing the file input produced.

   WHICH PICTURE THE CAMERA GIVES
   ------------------------------
   Asking for a size the camera doesn't have makes the browser crop. The first
   version asked for 3840x2160; the MacBook Air camera has no such mode, and
   Chrome answered with a 1080x1920 slice of its square sensor — half the width
   cut away (the "too zoomed in" view) and fewer pixels than it had to give.
   So the camera is opened, asked what it can do, and then asked for exactly
   its own largest frame with no cropping (resizeMode "none"). On that Mac that
   is 1552x1552, the whole field of view. On a phone it is the full back camera.
   Zoom goes to its widest and focus to continuous where the camera has them.

   WHY A REVIEW STEP
   -----------------
   A laptop camera has fixed focus: a receipt held too close is a blur, and
   Tesseract reads nothing from a blur. Seeing the photo before it goes costs
   one tap and saves a minute of reading a picture that was never going to work.

   getUserMedia needs a secure page (https or localhost). Receipt.jsx checks
   that first and falls back to the capture input, which on a phone or tablet
   opens the camera app instead. */

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon.jsx";

export const cameraAvailable = () =>
  typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;

function explain(err) {
  switch (err?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "The camera is blocked for this page. Allow it from the camera icon in the address bar, then try again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No camera was found on this device.";
    case "NotReadableError":
      return "Another app is using the camera. Close it and try again.";
    default:
      return `The camera didn't start (${err?.name || "unknown error"}).`;
  }
}

function stop(stream) {
  stream?.getTracks().forEach(t => t.stop());
}

/* The camera's own largest frame, uncropped, zoomed out, focusing. Every part
   is optional: a camera that refuses one keeps whatever it already gave. */
async function sharpest(track) {
  const caps = track.getCapabilities?.() || {};
  const want = { resizeMode: "none" };
  if (caps.width?.max && caps.height?.max) {
    want.width = { ideal: caps.width.max };
    want.height = { ideal: caps.height.max };
  }
  const advanced = [];
  if (caps.zoom?.min != null) advanced.push({ zoom: caps.zoom.min });
  if (caps.focusMode?.includes("continuous")) advanced.push({ focusMode: "continuous" });
  try {
    await track.applyConstraints(advanced.length ? { ...want, advanced } : want);
  } catch {
    try { await track.applyConstraints(want); } catch { /* keep what we have */ }
  }
}

export default function ReceiptCamera({ onPhoto, onClose, onImport }) {
  const video = useRef(null);
  const shutter = useRef(null);
  const [ready, setReady] = useState(false);
  const [ratio, setRatio] = useState(null);          // the frame's real shape, so the guide sits on the picture
  const [problem, setProblem] = useState(null);
  const [shot, setShot] = useState(null);            // { file, url } while they look at it

  useEffect(() => {
    let stream = null;
    let gone = false;                                // StrictMode mounts twice: the first stream must be released
    (async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1920 }, resizeMode: "none" },
        });
        if (gone) { stop(s); return; }
        stream = s;
        await sharpest(s.getVideoTracks()[0]);
        if (gone) return;
        const v = video.current;
        v.srcObject = s;
        await v.play();
        if (gone) return;
        setRatio(v.videoWidth && v.videoHeight ? v.videoWidth / v.videoHeight : null);
        setReady(true);
        shutter.current?.focus();
      } catch (err) {
        if (!gone) setProblem(explain(err));
      }
    })();
    return () => { gone = true; stop(stream); };
  }, []);

  useEffect(() => () => { if (shot) URL.revokeObjectURL(shot.url); }, [shot]);

  const take = useCallback(() => {
    const v = video.current;
    if (!ready || !v?.videoWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = v.videoWidth;
    canvas.height = v.videoHeight;
    canvas.getContext("2d").drawImage(v, 0, 0);
    canvas.toBlob((blob) => {
      if (!blob) { setProblem("The photo couldn't be taken. Try again."); return; }
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      const file = new File([blob], `receipt-${stamp}.jpg`, { type: "image/jpeg" });
      setShot({ file, url: URL.createObjectURL(blob) });
    }, "image/jpeg", 0.95);
  }, [ready]);

  const retake = () => { setShot(null); requestAnimationFrame(() => shutter.current?.focus()); };

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="camera" role="dialog" aria-modal="true" aria-label="Photograph a receipt">
      <div className="camera-view">
        {problem ? (
          <div className="camera-problem" role="alert">
            <Icon name="camera" size={48} />
            <p>{problem}</p>
            <div className="camera-problem-actions">
              <button className="btn btn-primary" onClick={onImport}><Icon name="image" /> Import a photo instead</button>
              <button className="btn btn-ghost" onClick={onClose}>Close</button>
            </div>
          </div>
        ) : (
          <div className="camera-frame" style={ratio ? { "--ratio": ratio } : undefined}>
            {/* The stream keeps running under the photo, so Retake is instant. */}
            <video ref={video} playsInline muted aria-hidden="true" hidden={!!shot} />
            {shot && <img className="camera-shot" src={shot.url} alt="The photo you just took" />}
            {ready && !shot && <div className="camera-guide" aria-hidden="true" />}
          </div>
        )}
        {!problem && (
          <p className="camera-hint">
            {shot ? "Can you read the prices? If it's blurry, retake it."
              : ready ? "Fill the frame with the receipt. Too close goes blurry."
              : "Starting the camera…"}
          </p>
        )}
      </div>
      {!problem && (shot ? (
        <div className="camera-bar">
          <button className="camera-side" onClick={retake}>Retake</button>
          <button className="btn btn-primary camera-use" onClick={() => onPhoto(shot.file)} autoFocus>
            <Icon name="check" /> Use this photo
          </button>
          <span />
        </div>
      ) : (
        <div className="camera-bar">
          <button className="camera-side" onClick={onClose}>Cancel</button>
          <button ref={shutter} className="camera-shutter" onClick={take} disabled={!ready} aria-label="Take the photo" />
          <button className="camera-side is-end" onClick={onImport}>Import instead</button>
        </div>
      ))}
    </div>
  );
}
