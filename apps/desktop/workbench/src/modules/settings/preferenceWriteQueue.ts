/** Keep writes ordered while allowing the next write to recover from a rejected predecessor. */
export function createPreferenceWriteQueue() {
  let tail: Promise<void> = Promise.resolve();
  return (write: () => Promise<void>): Promise<void> => {
    const result = tail.catch(() => {}).then(write);
    tail = result;
    return result;
  };
}
