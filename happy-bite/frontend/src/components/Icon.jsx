/* Drawn icons. No files, no network, a few hundred bytes each.
   Category icons on stock rows (the useful signal there is expiry, not a
   photo of a courgette), plus the handful of interface glyphs. */

const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" };

const CATEGORY = {
  produce: <><path d="M6.5 17.5c-2-2.6-1.4-6.6 1.6-9.2 3-2.6 7-2.6 8.8-.2 1.8 2.4 1 6.4-2 9-3 2.6-6.6 2.8-8.4.4Z" /><path d="M9.5 10.5c1.6 1.4 3.4 3 5 4.6" /></>,
  dairy: <><path d="M9 3.5h6v3l2.2 3a3 3 0 0 1 .8 2v8a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 19.5v-8a3 3 0 0 1 .8-2L9 6.5Z" /><path d="M6 13h12" /></>,
  meat: <><path d="M5.5 13c0-4.4 3.4-7.5 7.5-7.5S20 8.4 20 12.3c0 3.4-2.6 6.2-6.2 6.2-1.6 0-2.6-.6-3.6-.6-1 0-1.4.6-2.4.6-1.4 0-2.3-1-2.3-2.4 0-1.2.6-1.8.6-3.1Z" /><circle cx="14" cy="12" r="2.6" /></>,
  seafood: <><path d="M3.5 12c3-3.6 6.4-5.4 9.6-5.4 3.2 0 5.8 1.9 7.4 5.4-1.6 3.5-4.2 5.4-7.4 5.4-3.2 0-6.6-1.8-9.6-5.4Z" /><path d="M16.5 9.6 20 6.6v10.8l-3.5-3" /></>,
  pantry: <><path d="M6.5 8.5h11l1.5 11c.1.9-.6 1.5-1.4 1.5H6.4c-.8 0-1.5-.6-1.4-1.5Z" /><path d="M9 8.5c0-2.4 1.3-4 3-4s3 1.6 3 4" /></>,
  seasoning: <><path d="M8 9h8l1 11.5c0 .8-.6 1.5-1.4 1.5H8.4c-.8 0-1.4-.7-1.4-1.5Z" /><path d="M9.5 9V4.5h5V9" /></>,
  frozen: <><path d="M12 3v18M4.2 7.5l15.6 9M19.8 7.5l-15.6 9" /></>,
  bakery: <><path d="M4.5 15c0-4.4 3.4-8 7.5-8s7.5 3.6 7.5 8c0 1.4-1 2.5-2.4 2.5H6.9C5.5 17.5 4.5 16.4 4.5 15Z" /><path d="M9 17.5c-.6-3.6.4-6.6 2-8.4M14 17.5c.4-3.6-.4-6.6-1.6-8.4" /></>,
  drinks: <><path d="M7 4h10l-1.4 5.4a4 4 0 0 1-3.9 3 4 4 0 0 1-3.9-3Z" /><path d="M12 12.4V20M8.5 20h7" /></>,
  other: <><circle cx="12" cy="12" r="7.5" /><path d="M9.5 9.5 14.5 14.5M14.5 9.5 9.5 14.5" /></>
};

const UI = {
  plus: <path d="M12 5v14M5 12h14" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  back: <path d="M15 5l-7 7 7 7" />,
  next: <path d="M9 5l7 7-7 7" />,
  send: <path d="M5 12h13M12 6l6 6-6 6" />,
  mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3" /></>,
  stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
  speaker: <><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z" /><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" /></>,
  mute: <><path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4Z" /><path d="M16 10l4 4M20 10l-4 4" /></>,
  chef: <><path d="M7 14.5V19a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1v-4.5" /><path d="M7 14.5a4 4 0 0 1-1.2-7.7A4.5 4.5 0 0 1 12 4a4.5 4.5 0 0 1 6.2 2.8A4 4 0 0 1 17 14.5Z" /><path d="M7 17h10" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  spark: <path d="M12 3.5l1.9 5.3 5.6 1.2-4.4 3.6 1.3 5.6L12 16.3l-4.4 2.9 1.3-5.6L4.5 10l5.6-1.2Z" />,
  receipt: <><path d="M6 3.5h12v17l-2-1.3-2 1.3-2-1.3-2 1.3-2-1.3-2 1.3Z" /><path d="M9 8h6M9 11.5h6M9 15h4" /></>,
  image: <><rect x="3.5" y="5" width="17" height="14" rx="2.5" /><circle cx="9" cy="10" r="1.8" /><path d="m4 17 5-4.5 4 3.5 3-2.5 4 3.5" /></>,
  clock: <><circle cx="12" cy="12" r="8" /><path d="M12 8v4.5l3 1.8" /></>,
  flame: <path d="M12 3.5c1 3 4.5 4.8 4.5 9.2A4.5 4.5 0 0 1 12 17.5a4.5 4.5 0 0 1-4.5-4.8c0-1.9 1-3.2 2-4 .2 1.7 1 2.6 2 3 0-2.4-.5-5.4.5-8.2Z" />,
  list: <path d="M8 7h11M8 12h11M8 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01" />,
  people: <><circle cx="9" cy="8.5" r="3" /><path d="M3.5 19a5.5 5.5 0 0 1 11 0" /><circle cx="17" cy="9.5" r="2.3" /><path d="M16 14.2a4.5 4.5 0 0 1 5 4.8" /></>,
  sliders: <path d="M5 7h9M18 7h1M5 17h3M12 17h7M16 5v4M10 15v4" />,
  dice: <><rect x="4" y="4" width="16" height="16" rx="3.5" /><path d="M9 9h.01M15 15h.01M15 9h.01M9 15h.01M12 12h.01" strokeWidth="2.6" /></>,
  home: <path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1Z" />,
  box: <><path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5Z" /><path d="M4 8.5 12 13l8-4.5M12 13v7" /></>,
  book: <><path d="M5 5.5A1.5 1.5 0 0 1 6.5 4H19v14H6.5A1.5 1.5 0 0 0 5 19.5Z" /><path d="M5 19.5A1.5 1.5 0 0 0 6.5 21H19" /></>,
  cart: <><path d="M3.5 4.5h2.2l2 10.5h10l1.8-7.5H7" /><circle cx="9.5" cy="19" r="1.3" /><circle cx="16.5" cy="19" r="1.3" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  trash: <path d="M5 7h14M10 7V5h4v2M7 7l1 12.5h8L17 7" />,
  gear: <><circle cx="12" cy="12" r="3" /><path d="M12 3.5v2.5M12 18v2.5M3.5 12H6M18 12h2.5M6 6l1.8 1.8M16.2 16.2 18 18M6 18l1.8-1.8M16.2 7.8 18 6" /></>
};

export function Icon({ name, size = 24, className = "", title }) {
  const g = UI[name] || CATEGORY[name] || CATEGORY.other;
  return (
    <svg className={"icon " + className} viewBox="0 0 24 24" width={size} height={size}
         aria-hidden={title ? undefined : true} role={title ? "img" : undefined} {...P}>
      {title && <title>{title}</title>}
      {g}
    </svg>
  );
}
