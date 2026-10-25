import { describe, expect, it } from 'vitest';
import { parseTrace } from '../src/lib/trace';
import sample from '../src/data/sample-trace.jsonl?raw';

const HARNESS_EVENTS = [
  'session_start',
  'model_request',
  'model_response',
  'permission',
  'tool_result',
  'compact',
  'session_end',
];

describe('parseTrace', () => {
  it('parses the sample trace', () => {
    const events = parseTrace(sample);
    expect(events.length).toBeGreaterThanOrEqual(10);
    expect(events[0].event).toBe('session_start');
    expect(events[events.length - 1].event).toBe('session_end');
    for (const e of events) {
      expect(HARNESS_EVENTS).toContain(e.event);
      expect(typeof e.ts).toBe('number');
    }
  });

  it('has the shapes the brief asks for', () => {
    const names = parseTrace(sample).map((e) => e.event);
    expect(names.filter((n) => n === 'model_request')).toHaveLength(3);
    expect(names.filter((n) => n === 'model_response')).toHaveLength(3);
    expect(names.filter((n) => n === 'permission')).toHaveLength(1);
    expect(names.filter((n) => n === 'tool_result')).toHaveLength(4);
    expect(parseTrace(sample).filter((e) => e.is_error === true)).toHaveLength(1);
  });

  it('throws on a non-JSON line and ignores blank lines', () => {
    expect(() => parseTrace('{bad')).toThrow();
    expect(parseTrace('\n{"ts":1,"event":"session_start","task":"x"}\n\n')).toHaveLength(1);
  });
});
