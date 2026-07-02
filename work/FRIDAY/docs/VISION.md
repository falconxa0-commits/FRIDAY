# Friday Vision-Grounded Control

Friday doesn't just click coordinates; she understands what she's seeing.

## Logic Flow

1. **User Request:** "Friday, click the 'Send' button."
2. **Screen Capture:** `ScreenReader` captures the current display.
3. **Vision Understanding:** `ScreenAnalyzer` sends the image to GPT-4o with a specific prompt to find the element description.
4. **Coordinate Extraction:** GPT-4o returns JSON coordinates (X, Y).
5. **Human-like Interaction:** `PCControl` moves the cursor and clicks at the target location.

## Capabilities

- **Natural Language Selectors:** Interact with buttons, links, or inputs using plain English descriptions.
- **Dynamic UI Handling:** Works across different screen resolutions and UI changes because it relies on visual semantic understanding, not hardcoded positions.

## Usage

In chat:
> "Friday, open the browser and click the 'Sign In' button on the top right."

Under the hood, Friday invokes:
`await pc_control.click_described("Sign In button on top right")`
