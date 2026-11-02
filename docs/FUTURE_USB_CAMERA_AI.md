# Future USB-camera and onboard AI dashcam

User plan, recorded 2026-11-02. This is a future expansion, not an installation
request or an implemented feature.

- The Botslab G980H four-channel dashcam is temporary. Keep its current import
  support while planning the additional camera system.
- Add USB cameras inside/on the car for a secondary dashcam setup.
- Use another Raspberry Pi 5 with an AI HAT for onboard video object detection
  and related detection features. This is a separate future device from the
  existing Pi 4 wake/radio controller.
- Keep video recording and AI processing onboard. Integrate recordings and
  detection events with the car's local archive and dashboard when implemented.
- Use the existing central Ethernet switch to connect the future Pi 5 to the
  other car computers. Full video transfer uses Ethernet/Wi-Fi; any future
  LoRaWAN detection reports must be small and encrypted under the existing policy.

Camera models/count/placement, Pi 5 memory, exact AI HAT/accelerator, detection
models/classes, recording formats, retention and event rules remain undecided.
Do not assume a specific accelerator SDK or detection accuracy before hardware
selection and testing. The future implementation should use existing Linux camera
and accelerator software where suitable, with stable camera identities and
separate channel metadata.

For now, do not install AI runtimes, enable camera capture or change the current
G980H setup. Return to this note when the user is ready to build the secondary
system.
