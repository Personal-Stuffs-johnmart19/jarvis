export type InputDevice = { id: number; name: string };
export type MicrophoneChoice = { value: string; label: string; unavailable: boolean };
type Translate = (message: string, ...values: unknown[]) => string;

/** Preserve OS names and device IDs; distinguish duplicates without guessing hardware. */
export function microphoneChoices(devices: InputDevice[], selected: string, translate: Translate): MicrophoneChoice[] {
  const counts = new Map<string, number>();
  for (const device of devices) counts.set(device.name, (counts.get(device.name) ?? 0) + 1);
  const choices = devices.map(device => ({
    value: String(device.id),
    label: (counts.get(device.name) ?? 0) > 1
      ? translate("{0} · Device {1}", device.name, device.id)
      : device.name,
    unavailable: false,
  }));
  if (selected && !choices.some(choice => choice.value === selected))
    choices.unshift({value: selected, label: translate("Selected microphone unavailable (device {0})", selected), unavailable: true});
  return choices;
}
