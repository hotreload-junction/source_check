import { CL, TH } from './theme';

/** "Source Trace" with the logo standing in for the capital S. */
export function Wordmark() {
  return (
    <span aria-label="Source Trace" style={{ display: 'inline-block', whiteSpace: 'nowrap' }}>
      <svg
        aria-hidden="true"
        viewBox="44 28 204 238"
        style={{ height: '1.25em', width: '1.07em', verticalAlign: '-0.22em', marginRight: '0.04em', overflow: 'visible' }}
      >
        <path d="M212 75C180 45 92 45 92 100C92 155 214 135 214 195C214 245 140 255 82 224" fill="none" stroke={TH.ink} strokeWidth="42" strokeLinecap="round" />
        <circle cx="212" cy="75" r="32" fill={CL.r} />
        <circle cx="82" cy="224" r="34" fill={CL.g} />
      </svg>
      <span aria-hidden="true">ource Trace</span>
    </span>
  );
}
