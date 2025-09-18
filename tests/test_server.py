import sys
import os
# Add src to the path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import asyncio
import tempfile
import base64
from mcp.server.fastmcp import Context, Image

# Now import the server module
from blender_open_mcp import server as server_module

class TestServerTools(unittest.TestCase):

    def test_set_ollama_url(self):
        """Test the set_ollama_url function."""
        ctx = Context()
        new_url = "http://localhost:12345"

        # Run the async function
        result = asyncio.run(server_module.set_ollama_url(ctx, new_url))

        self.assertEqual(result, f"Ollama URL set to: {new_url}")
        self.assertEqual(server_module._ollama_url, new_url)

    def test_render_image_bug(self):
        """
        Test the render_image tool to demonstrate the bug.
        This test will fail before the fix and pass after.
        """
        # Create a mock context object with an add_image method
        ctx = MagicMock()
        ctx.add_image = MagicMock()
        # also mock get_image to return the added image
        def get_image():
            if ctx.add_image.call_args:
                return ctx.add_image.call_args[0][0]
            return None
        ctx.get_image = get_image


        # 1. Create a dummy image file to represent the rendered output
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
            tmp_file.write(b"fake_image_data")
            correct_image_path = tmp_file.name

        # 2. Mock the Blender connection
        mock_blender_conn = MagicMock()

        # 3. Configure the mock send_command to return the correct path
        # This simulates the behavior of the addon
        mock_blender_conn.send_command.return_value = {
            "rendered": True,
            "output_path": correct_image_path,
            "resolution": [1920, 1080]
        }

        # 4. Patch get_blender_connection to return our mock
        with patch('blender_open_mcp.server.get_blender_connection', return_value=mock_blender_conn):

            # 5. Call the render_image tool
            # The bug is that it uses "render.png" instead of `correct_image_path`
            result = asyncio.run(server_module.render_image(ctx, file_path="render.png"))

            # 6. Assertions
            self.assertEqual(result, "Image Rendered Successfully.")

            # Check if the context now has an image
            ctx.add_image.assert_called_once()
            img = ctx.add_image.call_args[0][0]
            self.assertIsInstance(img, Image)

            # Verify the image data is correct
            with open(correct_image_path, "rb") as f:
                expected_data = base64.b64encode(f.read()).decode('utf-8')
            self.assertEqual(img.data, expected_data)

        # 7. Clean up the dummy file
        os.remove(correct_image_path)

if __name__ == '__main__':
    unittest.main()
