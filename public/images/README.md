# FixFlow image assets

Both images were created for this redesign with the built-in imagegen tool on 2026-09-30. They are illustrative workspace photographs, not photographs of the user's actual office. No epple photography, branding, fonts, or source code was copied.

- `developer-workspace.webp`: 1536 × 1024, 135,294 bytes. Hero and account background.
- `documentation-desk.webp`: 1000 × 667, 59,602 bytes. Documentation guide image.

The generated PNG originals were preserved in the image tool's output directory. Project copies were encoded with Sharp to WebP (quality 86 and 83 respectively); the second image was resized to 1000px wide. The application uses local `next/image` assets and does not depend on external image hosts.

## Hero prompt

Use case: photorealistic-natural. Asset type: wide website hero photograph for FixFlow, a software debugging and documentation workspace. Create a premium cinematic editorial photograph, landscape 3:2 aspect ratio. A beautifully restrained contemporary software engineer's studio at blue hour, no people, a large curved dark monitor on the RIGHT side with softly glowing tiny illegible code lines, a silver laptop in foreground right, black keyboard, charcoal desk and dark ribbed concrete wall. A small warm amber desk lamp on far right. Cool steel blue daylight from large architectural window, subtly visible foliage outside. The LEFT HALF must be calm dark navy negative space to support large white web typography overlaid in code. Natural realism, atmospheric depth, tactile materials, gentle film grain, understated technology editorial photography. Deep blue and charcoal palette with subtle warm amber accents. No logos, no watermark, no readable text, no symbols or fake UI cards. This is only the background photograph, not a screenshot of a website. Rich details on the right and clean darker space on left. Save the generated asset and return its local path.

## Documentation image prompt

Use case: photorealistic-natural. Asset type: editorial supporting image for FixFlow software documentation workspace, landscape 3:2. Photograph a sunlit minimalist software developer desk close-up, an open silver laptop showing softly unfocused dark code editor beside a stack of textured cream technical notebooks, an open notebook with simple geometric sketches, a dark blue pen and a small ceramic coffee cup. Architectural afternoon light and shadows. Off-white tabletop, soft slate blue and warm sand palette, tactile natural materials, restrained premium editorial aesthetic. Frame laptop to the left and notebooks to the right, visually simple. No people, no logos, no watermark, no legible text, no fake UI overlay. Photography only, not a website screenshot.
