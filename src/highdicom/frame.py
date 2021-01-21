import logging
from io import BytesIO
from typing import Optional, Union

import numpy as np
from PIL import Image
from pydicom.dataset import Dataset
from pydicom.encaps import encapsulate
from pydicom.pixel_data_handlers.numpy_handler import pack_bits
from pydicom.pixel_data_handlers.rle_handler import rle_encode_frame
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    JPEG2000Lossless,
    JPEGBaseline,
    RLELossless,
)

from highdicom.enum import (
    PhotometricInterpretationValues,
    PixelRepresentationValues,
    PlanarConfigurationValues,
)


logger = logging.getLogger(__name__)


def encode_frame(
    array: np.ndarray,
    transfer_syntax_uid: str,
    bits_allocated: int,
    bits_stored: int,
    photometric_interpretation: Union[PhotometricInterpretationValues, str],
    pixel_representation: Union[PixelRepresentationValues, int] = 0,
    planar_configuration: Optional[Union[PlanarConfigurationValues, int]] = None
) -> bytes:
    """Encodes pixel data of an individual frame.

    Parameters
    ----------
    array: numpy.ndarray
        Pixel data in form of an array with dimensions
        (Rows x Columns x SamplesPerPixel) in case of a color image and
        (Rows x Columns) in case of a monochrome image
    transfer_syntax_uid: int
        Transfer Syntax UID
    bits_allocated: int
        Number of bits that need to be allocated per pixel sample
    bits_stored: int
        Number of bits that are required to store a pixel sample
    photometric_interpretation: int
        Photometric interpretation
    pixel_representation: int, optional
        Whether pixel samples are represented as unsigned integers or
        2's complements
    planar_configuration: int, optional
        Whether color samples are conded by pixel (`R1G1B1R2G2B2...`) or
        by plane (`R1R2...G1G2...B1B2...`).

    Returns
    -------
    bytes
        Pixel data (potentially compressed in case of encapsulated format
        encoding, depending on the transfer snytax)

    Raises
    ------
    ValueError
        When `transfer_syntax_uid` is not supported or when
        `planar_configuration` is missing in case of a color image frame.

    """
    rows = array.shape[0]
    cols = array.shape[1]
    if array.ndim > 2:
        if planar_configuration is None:
            raise ValueError(
                'Planar configuration needs to be specified for encoding of '
                'color image frames.'
            )
        planar_configuration = PlanarConfigurationValues(
            planar_configuration
        ).value
        samples_per_pixel = array.shape[2]
    else:
        samples_per_pixel = 1

    pixel_representation = PixelRepresentationValues(
        pixel_representation
    ).value
    photometric_interpretation = PhotometricInterpretationValues(
        photometric_interpretation
    ).value

    uncompressed_transfer_syntaxes = {
        ExplicitVRLittleEndian,
        ImplicitVRLittleEndian,
    }
    compressed_transfer_syntaxes = {
        JPEGBaseline,
        JPEG2000Lossless,
        RLELossless,
    }
    supported_transfer_syntaxes = uncompressed_transfer_syntaxes.union(
        compressed_transfer_syntaxes
    )
    if transfer_syntax_uid not in supported_transfer_syntaxes:
        raise ValueError(
            f'Transfer Syntax "{transfer_syntax_uid}" is not supported. '
            'Only the following are supported: "{}"'.format(
                '", "'.join(supported_transfer_syntaxes)
            )
        )
    if transfer_syntax_uid in uncompressed_transfer_syntaxes:
        if bits_allocated == 1:
            if (rows * cols * samples_per_pixel) % 8 != 0:
                raise ValueError(
                    'Frame cannot be bit packed because its size is not a '
                    'multiple of 8.'
                )
            return pack_bits(array.flatten())
        else:
            return array.flatten().tobytes()
    else:
        compression_lut = {
            JPEGBaseline: (
                'jpeg',
                {
                    'quality': 95
                },
            ),
            JPEG2000Lossless: (
                'jpeg2000',
                {
                    'tile_size': None,
                    'num_resolutions': 1,
                    'irreversible': False,
                },
            ),
        }
        if transfer_syntax_uid in compression_lut.keys():
            image_format, kwargs = compression_lut[transfer_syntax_uid]
            image = Image.fromarray(array)
            with BytesIO() as buf:
                image.save(buf, format=image_format, **kwargs)
                data = buf.getvalue()
        elif transfer_syntax_uid == RLELossless:
            data = rle_encode_frame(array)
        else:
            raise ValueError(
                f'Transfer Syntax "{transfer_syntax_uid}" is not supported.'
            )
    return data


def decode_frame(
    value: bytes,
    transfer_syntax_uid: str,
    rows: int,
    columns: int,
    samples_per_pixel: int,
    bits_allocated: int,
    bits_stored: int,
    photometric_interpretation: Union[PhotometricInterpretationValues, str],
    pixel_representation: Union[PixelRepresentationValues, int] = 0,
    planar_configuration: Optional[Union[PlanarConfigurationValues, int]] = None
) -> np.ndarray:
    """Decodes pixel data of an individual frame.

    Parameters
    ----------
    value: bytes
        Pixel data of a frame (potentially compressed in case
        of encapsulated format encoding, depending on the transfer syntax)
    transfer_syntax_uid: str
        Transfer Syntax UID
    rows: int
        Number of pixel rows in the frame
    columns: int
        Number of pixel columns in the frame
    samples_per_pixel: int
        Number of (color) samples per pixel
    bits_allocated: int
        Number of bits that need to be allocated per pixel sample
    bits_stored: int
        Number of bits that are required to store a pixel sample
    photometric_interpretation: int
        Photometric interpretation
    pixel_representation: int, optional
        Whether pixel samples are represented as unsigned integers or
        2's complements
    planar_configuration: int, optional
        Whether color samples are conded by pixel (`R1G1B1R2G2B2...`) or
        by plane (`R1R2...G1G2...B1B2...`).

    Returns
    -------
    numpy.ndarray
        Decoded pixel data

    Raises
    ------
    ValueError
        When transfer syntax is not supported.

    """
    pixel_representation = PixelRepresentationValues(
        pixel_representation
    ).value
    photometric_interpretation = PhotometricInterpretationValues(
        photometric_interpretation
    ).value
    if samples_per_pixel > 1:
        if planar_configuration is None:
            raise ValueError(
                'Planar configuration needs to be specified for decoding of '
                'color image frames.'
            )
        planar_configuration = PlanarConfigurationValues(
            planar_configuration
        ).value

    # The pydicom library does currently not support reading individual frames.
    # This hack creates a small dataset containing only a single frame, which
    # can then be decoded using the pydicom API.
    file_meta = Dataset()
    file_meta.TransferSyntaxUID = transfer_syntax_uid
    ds = Dataset()
    ds.file_meta = file_meta
    ds.Rows = rows
    ds.Columns = columns
    ds.SamplesPerPixel = samples_per_pixel
    ds.PhotometricInterpretation = photometric_interpretation
    ds.PixelRepresentation = pixel_representation
    ds.PlanarConfiguration = planar_configuration
    ds.BitsAllocated = bits_allocated
    ds.BitsStored = bits_stored
    ds.HighBit = bits_stored - 1

    if transfer_syntax_uid.is_encapsulated:
        if (transfer_syntax_uid == JPEGBaseline and
                photometric_interpretation == 'RGB'):
            # RGB color images, which were not transformed into YCbCr color
            # space upon JPEG compression, need to be handled separately.
            # Pillow assumes that images were transformed into YCbCr color
            # space prior to JPEG compression. However, with photometric
            # interpretation RGB, no color transformation was performed.
            # Setting the value of "mode" to YCbCr signals Pillow to not
            # apply any color transformation upon decompression.
            image = Image.open(BytesIO(value))
            color_mode = 'YCbCr'
            image.tile = [(
                'jpeg',
                image.tile[0][1],
                image.tile[0][2],
                (color_mode, ''),
            )]
            image.mode = color_mode
            image.rawmode = color_mode
            return np.asarray(image)
        else:
            ds.PixelData = encapsulate(frames=[value])
    else:
        ds.PixelData = value

    return ds.pixel_array
