import sys
import os
import unittest
from unittest.mock import patch, MagicMock
import tempfile

# Mock bpy and its submodules before importing the addon module
bpy_mock = MagicMock()
bpy_mock.props = MagicMock()
sys.modules['bpy'] = bpy_mock
sys.modules['bpy.props'] = bpy_mock.props

# Add the root directory to the path to allow imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Now we can import the addon
import addon

class TestAddonBugs(unittest.TestCase):

    @patch('addon.requests.get')
    def test_hdri_temp_file_deleted(self, mock_requests_get):
        """
        Test that the temporary file for a downloaded HDRI is deleted.
        """
        # 1. Setup mocks
        # Mock requests.get to return a successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"fake hdri data"
        mock_requests_get.return_value = mock_response

        # Mock the JSON response for the asset files
        mock_files_response = MagicMock()
        mock_files_response.status_code = 200
        mock_files_response.json.return_value = {
            "hdri": {
                "1k": {
                    "hdr": {
                        "url": "http://fake.url/hdri.hdr"
                    }
                }
            }
        }
        # The first call to requests.get is for the files, the second is for the download
        mock_requests_get.side_effect = [mock_files_response, mock_response]

        # A very basic mock for bpy
        addon.bpy.data.images.load.return_value = MagicMock()
        addon.bpy.path.abspath.side_effect = lambda x: x # Return the path as is

        # 2. Instantiate the server from the addon
        server = addon.BlenderMCPServer()

        # 3. Call the function
        result = server.download_polyhaven_asset(
            asset_id="test_hdri",
            asset_type="hdris",
            resolution="1k",
            file_format="hdr"
        )

        # 4. Assertions
        self.assertTrue(result.get("success"))

        # Get the path of the temporary file that was created
        # The path is passed to bpy.data.images.load
        self.assertTrue(addon.bpy.data.images.load.called)
        temp_file_path = addon.bpy.data.images.load.call_args[0][0]

        # Assert that the temporary file no longer exists
        self.assertFalse(os.path.exists(temp_file_path), f"Temporary file was not deleted: {temp_file_path}")

if __name__ == '__main__':
    unittest.main()
