/** Open pages and a play button: the app's book-to-video mark. */
export default function BrandMark({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="180 282 664 494" fill="none" aria-hidden="true" focusable="false">
      <path d="M222 316C332 300 426 316 512 368C598 316 692 300 802 316V666C692 650 598 666 512 720C426 666 332 650 222 666Z"
        stroke="currentColor" strokeWidth="38" strokeLinejoin="round" />
      <path d="M512 368V720M292 433C336 433 376 444 421 463M292 519C336 519 376 530 421 549"
        stroke="currentColor" strokeWidth="32" strokeLinecap="round" />
      <path d="M628 421L743 494C753 500 753 508 743 514L628 587C616 595 606 589 606 575V433C606 419 616 413 628 421Z"
        fill="currentColor" />
    </svg>
  );
}
