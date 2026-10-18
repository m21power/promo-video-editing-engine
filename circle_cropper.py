import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw


class CircleCropper:
    def __init__(self, root):
        self.root = root
        self.root.title("Circle Image Cropper")
        self.root.geometry("950x750")
        self.root.configure(bg="#181818")

        # Original image
        self.original_image = None

        # Current transformed image
        self.image = None
        self.image_tk = None

        # Image position
        self.image_x = 0
        self.image_y = 0

        # Mouse dragging
        self.dragging = False
        self.last_mouse_x = 0
        self.last_mouse_y = 0

        # Canvas preview size
        self.canvas_size = 600

        # --------------------------------------------------
        # TOP
        # --------------------------------------------------

        top = tk.Frame(root, bg="#181818")
        top.pack(fill="x", padx=20, pady=15)

        tk.Label(
            top,
            text="Circle Image Cropper",
            font=("Arial", 22, "bold"),
            fg="white",
            bg="#181818"
        ).pack(side="left")

        tk.Button(
            top,
            text="Upload Image",
            command=self.upload_image,
            font=("Arial", 11, "bold"),
            padx=20,
            pady=8,
            relief="flat",
            cursor="hand2"
        ).pack(side="right")

        # --------------------------------------------------
        # CANVAS
        # --------------------------------------------------

        canvas_container = tk.Frame(
            root,
            bg="#252525"
        )

        canvas_container.pack(
            padx=20,
            pady=10
        )

        self.canvas = tk.Canvas(
            canvas_container,
            width=self.canvas_size,
            height=self.canvas_size,
            bg="#333333",
            highlightthickness=0,
            cursor="hand2"
        )

        self.canvas.pack()

        # Mouse events
        self.canvas.bind(
            "<ButtonPress-1>",
            self.start_drag
        )

        self.canvas.bind(
            "<B1-Motion>",
            self.drag_image
        )

        self.canvas.bind(
            "<ButtonRelease-1>",
            self.stop_drag
        )

        # --------------------------------------------------
        # RADIUS
        # --------------------------------------------------

        controls = tk.Frame(
            root,
            bg="#181818"
        )

        controls.pack(
            fill="x",
            padx=50,
            pady=10
        )

        tk.Label(
            controls,
            text="Circle Radius",
            font=("Arial", 11, "bold"),
            fg="white",
            bg="#181818"
        ).grid(row=0, column=0, sticky="w")

        self.radius_value = tk.Label(
            controls,
            text="200 px",
            fg="#aaaaaa",
            bg="#181818",
            width=10
        )

        self.radius_value.grid(
            row=0,
            column=1
        )

        self.radius_slider = tk.Scale(
            controls,
            from_=50,
            to=280,
            orient="horizontal",
            length=550,
            command=self.radius_changed,
            bg="#181818",
            fg="white",
            highlightthickness=0,
            troughcolor="#444444"
        )

        self.radius_slider.set(200)

        self.radius_slider.grid(
            row=0,
            column=2
        )

        # --------------------------------------------------
        # ZOOM
        # --------------------------------------------------

        tk.Label(
            controls,
            text="Image Zoom",
            font=("Arial", 11, "bold"),
            fg="white",
            bg="#181818"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(15, 0)
        )

        self.zoom_value = tk.Label(
            controls,
            text="100%",
            fg="#aaaaaa",
            bg="#181818",
            width=10
        )

        self.zoom_value.grid(
            row=1,
            column=1,
            pady=(15, 0)
        )

        self.zoom_slider = tk.Scale(
            controls,
            from_=25,
            to=300,
            orient="horizontal",
            length=550,
            command=self.zoom_changed,
            bg="#181818",
            fg="white",
            highlightthickness=0,
            troughcolor="#444444"
        )

        self.zoom_slider.set(100)

        self.zoom_slider.grid(
            row=1,
            column=2,
            pady=(15, 0)
        )

        # --------------------------------------------------
        # BUTTONS
        # --------------------------------------------------

        buttons = tk.Frame(
            root,
            bg="#181818"
        )

        buttons.pack(
            pady=15
        )

        tk.Button(
            buttons,
            text="Reset Position",
            command=self.reset_position,
            font=("Arial", 11),
            padx=20,
            pady=8,
            relief="flat"
        ).pack(
            side="left",
            padx=5
        )

        tk.Button(
            buttons,
            text="Save Transparent PNG",
            command=self.save_image,
            font=("Arial", 11, "bold"),
            bg="#4CAF50",
            fg="white",
            padx=25,
            pady=9,
            relief="flat",
            cursor="hand2"
        ).pack(
            side="left",
            padx=5
        )

        # --------------------------------------------------
        # INFO
        # --------------------------------------------------

        self.info = tk.Label(
            root,
            text="Upload an image, then drag it inside the circle.",
            font=("Arial", 10),
            fg="#999999",
            bg="#181818"
        )

        self.info.pack()

    # ======================================================
    # UPLOAD
    # ======================================================

    def upload_image(self):

        path = filedialog.askopenfilename(
            title="Select Image",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.webp"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("WebP", "*.webp"),
            ]
        )

        if not path:
            return

        try:

            self.original_image = Image.open(
                path
            ).convert("RGBA")

            # Start with 100% zoom
            self.zoom_slider.set(100)

            # Reset position
            self.image_x = self.canvas_size // 2
            self.image_y = self.canvas_size // 2

            self.update_preview()

        except Exception as e:

            messagebox.showerror(
                "Error",
                f"Could not open image:\n{e}"
            )

    # ======================================================
    # RADIUS
    # ======================================================

    def radius_changed(self, value):

        radius = int(float(value))

        self.radius_value.config(
            text=f"{radius} px"
        )

        self.update_preview()

    # ======================================================
    # ZOOM
    # ======================================================

    def zoom_changed(self, value):

        zoom = int(float(value))

        self.zoom_value.config(
            text=f"{zoom}%"
        )

        self.update_preview()

    # ======================================================
    # MOUSE DRAG
    # ======================================================

    def start_drag(self, event):

        if self.original_image is None:
            return

        self.dragging = True

        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

    def drag_image(self, event):

        if not self.dragging:
            return

        dx = event.x - self.last_mouse_x
        dy = event.y - self.last_mouse_y

        self.image_x += dx
        self.image_y += dy

        self.last_mouse_x = event.x
        self.last_mouse_y = event.y

        self.update_preview()

    def stop_drag(self, event):

        self.dragging = False

    # ======================================================
    # RESET
    # ======================================================

    def reset_position(self):

        if self.original_image is None:
            return

        self.image_x = self.canvas_size // 2
        self.image_y = self.canvas_size // 2

        self.zoom_slider.set(100)

        self.update_preview()

    # ======================================================
    # CREATE IMAGE
    # ======================================================

    def create_result(self):

        if self.original_image is None:
            return None

        radius = int(self.radius_slider.get())
        diameter = radius * 2

        # ----------------------------------------------
        # Final transparent image
        # ----------------------------------------------

        result = Image.new(
            "RGBA",
            (diameter, diameter),
            (0, 0, 0, 0)
        )

        # ----------------------------------------------
        # Resize according to zoom
        # ----------------------------------------------

        zoom = self.zoom_slider.get() / 100

        width = max(
            1,
            int(self.original_image.width * zoom)
        )

        height = max(
            1,
            int(self.original_image.height * zoom)
        )

        resized = self.original_image.resize(
            (width, height),
            Image.Resampling.LANCZOS
        )

        # ----------------------------------------------
        # Create a transparent layer the same size
        # as the final circle
        # ----------------------------------------------

        image_layer = Image.new(
            "RGBA",
            (diameter, diameter),
            (0, 0, 0, 0)
        )

        # ----------------------------------------------
        # Position image
        # ----------------------------------------------

        canvas_center = self.canvas_size / 2

        offset_x = self.image_x - canvas_center
        offset_y = self.image_y - canvas_center

        paste_x = int(
            radius + offset_x - width / 2
        )

        paste_y = int(
            radius + offset_y - height / 2
        )

        # ----------------------------------------------
        # Paste image WITHOUT mask
        # ----------------------------------------------

        image_layer.alpha_composite(
            resized,
            (paste_x, paste_y)
        )

        # ----------------------------------------------
        # Create circle mask
        # ----------------------------------------------

        mask = Image.new(
            "L",
            (diameter, diameter),
            0
        )

        draw = ImageDraw.Draw(mask)

        draw.ellipse(
            (
                0,
                0,
                diameter - 1,
                diameter - 1
            ),
            fill=255
        )

        # ----------------------------------------------
        # Apply circular mask
        # ----------------------------------------------

        result.paste(
            image_layer,
            (0, 0),
            mask
        )

        return result

    # ======================================================
    # PREVIEW
    # ======================================================

    def update_preview(self):

        self.canvas.delete("all")

        if self.original_image is None:

            self.canvas.create_text(
                self.canvas_size // 2,
                self.canvas_size // 2,
                text="Upload an image",
                fill="#aaaaaa",
                font=("Arial", 18)
            )

            return

        radius = int(
            self.radius_slider.get()
        )

        # ----------------------------------------------
        # Create transparent checkerboard
        # ----------------------------------------------

        self.draw_checkerboard()

        # ----------------------------------------------
        # Prepare image
        # ----------------------------------------------

        zoom = self.zoom_slider.get() / 100

        width = int(
            self.original_image.width * zoom
        )

        height = int(
            self.original_image.height * zoom
        )

        resized = self.original_image.resize(
            (width, height),
            Image.Resampling.LANCZOS
        )

        # ----------------------------------------------
        # Create temporary canvas
        # ----------------------------------------------

        temp = Image.new(
            "RGBA",
            (
                self.canvas_size,
                self.canvas_size
            ),
            (0, 0, 0, 0)
        )

        paste_x = int(
            self.image_x - width / 2
        )

        paste_y = int(
            self.image_y - height / 2
        )

        temp.alpha_composite(
            resized,
            (paste_x, paste_y)
        )

        # ----------------------------------------------
        # Circle mask
        # ----------------------------------------------

        mask = Image.new(
            "L",
            (
                self.canvas_size,
                self.canvas_size
            ),
            0
        )

        draw = ImageDraw.Draw(mask)

        center = self.canvas_size // 2

        draw.ellipse(
            (
                center - radius,
                center - radius,
                center + radius,
                center + radius
            ),
            fill=255
        )

        # ----------------------------------------------
        # Apply circle mask
        # ----------------------------------------------

        clipped = Image.new(
            "RGBA",
            (
                self.canvas_size,
                self.canvas_size
            ),
            (0, 0, 0, 0)
        )

        clipped.paste(
            temp,
            (0, 0),
            mask
        )

        # ----------------------------------------------
        # Show image
        # ----------------------------------------------

        self.image_tk = ImageTk.PhotoImage(
            clipped
        )

        self.canvas.create_image(
            0,
            0,
            anchor="nw",
            image=self.image_tk
        )

        # ----------------------------------------------
        # Circle border
        # ----------------------------------------------

        self.canvas.create_oval(
            center - radius,
            center - radius,
            center + radius,
            center + radius,
            outline="white",
            width=2
        )

    # ======================================================
    # CHECKERBOARD
    # ======================================================

    def draw_checkerboard(self):

        size = 25

        for y in range(
            0,
            self.canvas_size,
            size
        ):

            for x in range(
                0,
                self.canvas_size,
                size
            ):

                if (
                    (x // size) +
                    (y // size)
                ) % 2 == 0:

                    color = "#444444"

                else:

                    color = "#333333"

                self.canvas.create_rectangle(
                    x,
                    y,
                    x + size,
                    y + size,
                    fill=color,
                    outline=""
                )

    # ======================================================
    # SAVE
    # ======================================================

    def save_image(self):

        if self.original_image is None:

            messagebox.showwarning(
                "No Image",
                "Please upload an image first."
            )

            return

        output = filedialog.asksaveasfilename(
            title="Save Circle Image",
            defaultextension=".png",
            filetypes=[
                ("PNG Image", "*.png")
            ]
        )

        if not output:
            return

        try:

            result = self.create_result()

            result.save(
                output,
                "PNG"
            )

            messagebox.showinfo(
                "Success",
                "Transparent circle image saved successfully!"
            )

        except Exception as e:

            messagebox.showerror(
                "Error",
                f"Could not save image:\n{e}"
            )


# ==========================================================
# START APPLICATION
# ==========================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = CircleCropper(root)

    root.mainloop()
