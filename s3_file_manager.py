# External imports
from pathlib import Path
import os
import boto3
import tempfile
from botocore.exceptions import NoCredentialsError, ClientError
import logging
import tempfile
from dotenv import load_dotenv
import os
import time

# Load the environment variables
load_dotenv()


class S3FileManager:
    """
    A class to interact with AWS S3.

    Attributes:
    -----------
    aws_access_key_id: str
        The AWS access key ID.
    aws_secret_access_key: str
        The AWS secret access key.
    bucket_name: str
        The name of the bucket.
    s3_client: S3 client
        The S3 client.


    Methods:
    --------
    upload_file(file_path, key)
        Upload a file to S3.
    upload_temp_file(file, key)
        Upload a temporary file to S3.
    list_files(key)
        List all files in the S3 bucket with the given key.
    download_file(key, download_path)
        Download a file from S3.
    delete_file(key)
        Delete a file from S3.
    upload_file_from_bytes(data, key)
        Upload a file to S3 from bytes.
    download_file_to_bytes(key)
        Download a file from S3 to bytes.
    get_object(key)
        Get an object from S3.

    """

    def __init__(self):
        """
        Constructor for the S3FileManager class.
        """

        # Initialize AWS credentials and S3 client
        self.aws_access_key_id = os.getenv("AWS_ACCESS_KEY")
        self.aws_secret_access_key = os.getenv("AWS_SECRET_KEY")
        self.bucket_name = os.getenv("AWS_BUCKET_NAME")
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key
        )

    async def upload_video(self, file_path, key):
        """
        Upload a video file to S3
        
        Args:
        file_path: str - path to the video file to be uploaded
        key: str - key to be used in the S3 bucket
        
        Returns:
        bool: True if the file was uploaded successfully, False otherwise
        """
        try:
            self.s3_client.upload_file(file_path, self.bucket_name, key, ExtraArgs={'ContentType': 'video/mp4'})
            self.make_object_public(key)
            return True
        except FileNotFoundError:
            logging.error("The file was not found")
            return False
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        
    def change_content_type(self,
                        key: str,
                        destination_stream: str,
                        make_public: bool = False) -> bool:
        """
        Update the Content-Type of an existing S3 object *in-place*.

        Parameters
        ----------
        key : str
            Object key in the bucket.
        destination_stream : str
            New MIME type, e.g. "application/pdf".
        make_public : bool
            If True, sets ACL='public-read' after the copy.

        Returns
        -------
        bool
            True on success, False on error.
        """
        try:
            # HEAD request to copy original metadata except ContentType
            print(key)
            head = self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=key,
            )
            new_meta = head.get("Metadata", {})        # preserve user metadata

            # In-place copy with "REPLACE" tells S3 to overwrite metadata
            self.s3_client.copy_object(
                Bucket=self.bucket_name,
                Key=key,
                CopySource={"Bucket": self.bucket_name, "Key": key},
                Metadata=new_meta,
                MetadataDirective="REPLACE",
                ContentType=destination_stream,
                ContentDisposition="inline"
            )

            if make_public:
                self.s3_client.put_object_acl(
                    ACL="public-read", Bucket=self.bucket_name, Key=key
                )

            logging.info("ContentType for %s set to %s", key, destination_stream)
            return True

        except (self.s3_client.exceptions.NoSuchKey, ClientError) as exc:
            print(exc)
            logging.error("Failed to change ContentType for %s – %s", key, exc)
            return False


    def upload_file_obj(self, file_obj, key):
        """
        Upload a file object to S3

        Args:
        file_obj: file object - file object to be uploaded
        key: str - key to be used in the S3 bucket

        Returns:
        bool: True if the file was uploaded successfully, False otherwise
        """
        try:
            self.s3_client.upload_fileobj(file_obj, self.bucket_name, key)
            self.make_object_public(key)
            return True
        except FileNotFoundError:
            logging.error("The file was not found")
            return False
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        
    async def upload_file_from_frontend(self, file, key):
        try:
            file_content = await file.read()
            self.s3_client.put_object(Bucket=self.bucket_name, Key=key, Body=file_content)
            self.make_object_public(key)
            return True
        except FileNotFoundError:
            logging.error("The file was not found")
            return False
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        

    async def upload_file(self, file_path, key, content_type=None):
        """
        Upload a file to S3

        Args:
        file_path: str - path to the file to be uploaded
        key: str - key to be used in the S3 bucket

        Returns:
        bool: True if the file was uploaded successfully, False otherwise
        """
        try:
            if content_type:
                self.s3_client.upload_file(file_path, self.bucket_name, key, ExtraArgs={'ContentType': content_type})
            else:
                self.s3_client.upload_file(file_path, self.bucket_name, key)
            self.make_object_public(key)
            return True
        except FileNotFoundError:
            logging.error("The file was not found")
            return False
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False

    def upload_temp_file(self, file, key):
        """
        Upload a temporary file to S3

        Args:
        file: bytes - file data to be uploaded
        key: str - key to be used in the S3 bucket

        Returns:
        bool: True if the file was uploaded successfully, False otherwise
        """
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(file)
                temp_file.close()
                self.upload_file(temp_file.name, key)
                os.unlink(temp_file.name)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        
    def make_object_public(self, key):
        try:
            self.s3_client.put_object_acl(
                ACL='public-read', Bucket=self.bucket_name, Key=key)
            logging.info(
                f"Object '{key}' made public in S3 bucket '{self.bucket_name}'")
            return True
        except NoCredentialsError:
            logging.error("AWS credentials not available or incorrect.")
            return False
        except Exception as e:
            logging.error(f"An error occurred: make_object_public: {e}")
            return False
        
    def copy_file(self, source_key, destination_key):
        """
        Copy a file in S3

        Args:
        source_key: str - key of the source file in the S3 bucket
        destination_key: str - key of the destination file in the S3 bucket

        Returns:
        bool: True if the file was copied successfully, False otherwise
        """
        try:
            self.s3_client.copy_object(
                Bucket=self.bucket_name, CopySource=f"{self.bucket_name}/{source_key}", Key=destination_key)
            self.make_object_public(destination_key)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False

    def list_files(self, key):
        """
        List all files in the S3 bucket with the given key

        Args:
        key: str - key of the files in the S3 bucket

        Returns:
        list: List of files in the S3 bucket with the given key
        """
        try:
            files = []
            continuation_token = None
            while True:
                if continuation_token:
                    response = self.s3_client.list_objects_v2(
                        Bucket=self.bucket_name, Prefix=key, ContinuationToken=continuation_token)
                else:
                    response = self.s3_client.list_objects_v2(
                        Bucket=self.bucket_name, Prefix=key)
                files.extend(response.get("Contents", []))
                continuation_token = response.get("NextContinuationToken")
                if not continuation_token:
                    break
            return files
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False


    def download_file(self, key, download_path):
        """
        Download a file from S3

        Args:
        key: str - key of the file in the S3 bucket
        download_path: str - path to download the file

        Returns:
        bool: True if the file was downloaded successfully, False otherwise
        """
        try:
            if os.path.exists(download_path):
                os.remove(download_path)
            with open(download_path, 'wb') as f:
                self.s3_client.download_fileobj(self.bucket_name, key, f)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        

    def delete_file(self, key):
        """
        Delete a file from S3

        Args:
        key: str - key of the file in the S3 bucket

        Returns:
        bool: True if the file was deleted successfully, False otherwise
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False

    def upload_file_from_bytes(self, data, key):
        """
        Upload a file to S3 from bytes

        Args:
        data: bytes - data to be uploaded
        key: str - key to be used in the S3 bucket

        Returns:
        bool: True if the file was uploaded successfully, False otherwise
        """
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.write(data)
                temp_file.close()
                print("Temp file name: ", temp_file.name)
                self.upload_file(temp_file.name, key)
                os.unlink(temp_file.name)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False

    def download_file_to_bytes(self, key):
        """
        Download a file from S3 to bytes

        Args:
        key: str - key of the file in the S3 bucket

        Returns:
        bytes: data of the file
        """
        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file.close()
                self.download_file(key, temp_file.name)
                with open(temp_file.name, 'rb') as f:
                    data = f.read()
                os.unlink(temp_file.name)
            return data
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False

    def get_object(self, key):
        """
        Get an object from S3

        Args:
        key: str - key of the object in the S3 bucket

        Returns:
        bytes: data of the object
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name, Key=key)
            return response
        except NoCredentialsError:
            logging.error("Credentials not available")
            return None
        except ClientError as e:
            logging.error(e)
            return None

    async def upload_directory(self, directory_path, key):
        """
        Upload a directory to S3

        Args:
        directory_path: str - path to the directory to be uploaded
        key: str - key to be used in the S3 bucket

        Returns:
        bool: True if the directory was uploaded successfully, False otherwise
        """
        print("Uploading directory: ", directory_path)
        print("Key: ", key)
        try:
            for root, dirs, files in os.walk(directory_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    s3_key = key + file_path[len(directory_path):]
                    print("Uploading file: ", file_path)
                    print("S3 key for the file: ", s3_key)
                    await self.upload_file(file_path, s3_key)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        

    def download_directory(self, key, download_path):
        """
        Download a directory from S3

        Args:
        key: str - key of the directory in the S3 bucket
        download_path: str - path to download the directory

        Returns:
        bool: True if the directory was downloaded successfully, False otherwise
        """
        try:
            for obj in self.list_files(key):
                file_key = obj['Key']
                file_path = os.path.join(download_path, file_key[len(key):])
                self.download_file(file_key, file_path)
            return True
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False

    async def save_mp3_and_upload(self, audio_data, key):
        """
        Save the MP3 data to a file and upload it to S3.
        
        Args:
        audio_data: bytes - The audio data (MP3).
        key: str - The key under which to store the file in S3.
        
        Returns:
        bool: True if the upload succeeded, False otherwise.
        """
        try:
            # Define the folder where you want to save the file
            folder_path = "mp3_files"
            Path(folder_path).mkdir(parents=True, exist_ok=True)  # Create the folder if it doesn't exist
            
            # Create a unique filename using the current timestamp
            file_name = f"podcast_audio_{int(time.time())}.mp3"
            file_path = os.path.join(folder_path, file_name)
            
            # Write the audio data to an MP3 file
            with open(file_path, 'wb') as f:
                f.write(audio_data)

            # Upload the file asynchronously
            success = await self.async_upload_file(file_path, key)
            
            if success:
                logging.info(f"Successfully uploaded file to S3 with key: {key}")
                return True
            else:
                logging.error(f"Failed to upload file to S3 with key: {key}")
                return False

        except Exception as e:
            logging.error(f"Error saving and uploading MP3: {e}")
            return False  # Return False if there's an exception

        finally:
            # Clean up: Delete the file after uploading
            if 'file_path' in locals() and os.path.exists(file_path):
                os.remove(file_path)


    def async_upload_file(self, file_path, key):
        """
        Upload a file to S3.

        Args:
        file_path (str): Path to the file on your local machine.
        key (str): Key (path) in the S3 bucket where the file will be stored.

        Returns:
        bool: True if the file was uploaded successfully, False otherwise.
        """
        try:
            # Upload the file to S3
            response = self.s3_client.upload_file(file_path, self.bucket_name, key)
            logging.info(f"Successfully uploaded file to S3 with key: {key}")
            return True
        except Exception as e:
            logging.error(f"Error uploading file to S3: {e}")
            return False

    async def upload_png_image(self, file_path, key):
        """
        Upload a PNG image file to S3

        Args:
        file_path: str - path to the PNG image file to be uploaded
        key: str - key to be used in the S3 bucket

        Returns:
        bool: True if the file was uploaded successfully, False otherwise
        """
        try:
            self.s3_client.upload_file(file_path, self.bucket_name, key, ExtraArgs={'ContentType': 'image/png'})
            self.make_object_public(key)
            return True
        except FileNotFoundError:
            logging.error("The file was not found")
            return False
        except NoCredentialsError:
            logging.error("Credentials not available")
            return False
        except ClientError as e:
            logging.error(e)
            return False
        except Exception as e:
            logging.error(f"An error occurred: {e}")
            return False
