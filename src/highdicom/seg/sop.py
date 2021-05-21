"""Module for the SOP class of the Segmentation IOD."""
from copy import deepcopy
from collections import OrderedDict
import logging
import numpy as np
from collections import defaultdict
from typing import (
    Any, Dict, Iterable, List, Optional, Set, Sequence, Union, Tuple
)

from pydicom.dataset import Dataset
from pydicom.encaps import decode_data_sequence, encapsulate
from pydicom.multival import MultiValue
from pydicom.pixel_data_handlers.numpy_handler import pack_bits
from pydicom.pixel_data_handlers.util import get_expected_length
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    JPEG2000Lossless,
    RLELossless,
    UID,
)
from pydicom.sr.codedict import codes
from pydicom.sr.coding import Code

from highdicom.base import SOPClass
from highdicom.content import (
    PlaneOrientationSequence,
    PlanePositionSequence,
    PixelMeasuresSequence
)
from highdicom.errors import DicomAttributeError
from highdicom.enum import CoordinateSystemNames
from highdicom.frame import encode_frame
from highdicom.seg.content import (
    DimensionIndexSequence,
    SegmentDescription,
)
from highdicom.seg.enum import (
    SegmentationFractionalTypeValues,
    SegmentationTypeValues,
    SegmentsOverlapValues,
    SpatialLocationsPreservedValues,
    SegmentAlgorithmTypeValues,
)
from highdicom.seg.utils import iter_segments
from highdicom.sr.coding import CodedConcept


logger = logging.getLogger(__name__)


_NO_FRAME_REF_VALUE = -1


class Segmentation(SOPClass):

    """SOP class for a Segmentation, which represents one or more
    regions of interest (ROIs) as mask images (raster graphics) in
    two-dimensional image space.

    """

    def __init__(
            self,
            source_images: Sequence[Dataset],
            pixel_array: np.ndarray,
            segmentation_type: Union[str, SegmentationTypeValues],
            segment_descriptions: Sequence[SegmentDescription],
            series_instance_uid: str,
            series_number: int,
            sop_instance_uid: str,
            instance_number: int,
            manufacturer: str,
            manufacturer_model_name: str,
            software_versions: Union[str, Tuple[str]],
            device_serial_number: str,
            fractional_type: Optional[
                Union[str, SegmentationFractionalTypeValues]
            ] = SegmentationFractionalTypeValues.PROBABILITY,
            max_fractional_value: int = 255,
            content_description: Optional[str] = None,
            content_creator_name: Optional[str] = None,
            transfer_syntax_uid: Union[str, UID] = ImplicitVRLittleEndian,
            pixel_measures: Optional[PixelMeasuresSequence] = None,
            plane_orientation: Optional[PlaneOrientationSequence] = None,
            plane_positions: Optional[Sequence[PlanePositionSequence]] = None,
            **kwargs: Any
        ) -> None:
        """
        Parameters
        ----------
        source_images: Sequence[Dataset]
            One or more single- or multi-frame images (or metadata of images)
            from which the segmentation was derived
        pixel_array: numpy.ndarray
            Array of segmentation pixel data of boolean, unsigned integer or
            floating point data type representing a mask image. If `pixel_array`
            is a floating-point array or a binary array (containing only the
            values ``True`` and ``False`` or ``0`` and ``1``), the segment
            number used to encode the segment is taken from
            `segment_descriptions`. Otherwise, if `pixel_array` contains
            multiple integer values, each value is treated as a different
            segment whose segment number is that integer value. In this case,
            all segments found in the array must be described
            in `segment_descriptions`. Note that this is valid for both
            ``"BINARY"`` and ``"FRACTIONAL"`` segmentations.
            For ``"FRACTIONAL"`` segmentations, values either encode the
            probability of a given pixel belonging to a segment
            (if `fractional_type` is ``"PROBABILITY"``)
            or the extent to which a segment occupies the pixel
            (if `fractional_type` is ``"OCCUPANCY"``).
            When `pixel_array` has a floating point data type, only one
            segment can be encoded. Additional segments can be subsequently
            added to the `Segmentation` instance using the ``add_segments()``
            method.
            If `pixel_array` represents a 3D image, the first dimension
            represents individual 2D planes and these planes must be ordered
            based on their position in the three-dimensional patient
            coordinate system (first along the X axis, second along the Y axis,
            and third along the Z axis).
            If `pixel_array` represents a tiled 2D image, the first dimension
            represents individual 2D tiles (for one channel and z-stack) and
            these tiles must be ordered based on their position in the tiled
            total pixel matrix (first along the row dimension and second along
            the column dimension, which are defined in the three-dimensional
            slide coordinate system by the direction cosines encoded by the
            *Image Orientation (Slide)* attribute).
        segmentation_type: Union[str, highdicom.seg.SegmentationTypeValues]
            Type of segmentation, either ``"BINARY"`` or ``"FRACTIONAL"``
        segment_descriptions: Sequence[highdicom.seg.SegmentDescription]
            Description of each segment encoded in `pixel_array`. In the case of
            pixel arrays with multiple integer values, the segment description
            with the corresponding segment number is used to describe each segment.
        series_instance_uid: str
            UID of the series
        series_number: Union[int, None]
            Number of the series within the study
        sop_instance_uid: str
            UID that should be assigned to the instance
        instance_number: int
            Number that should be assigned to the instance
        manufacturer: str
            Name of the manufacturer of the device (developer of the software)
            that creates the instance
        manufacturer_model_name: str
            Name of the device model (name of the software library or
            application) that creates the instance
        software_versions: Union[str, Tuple[str]]
            Version(s) of the software that creates the instance
        fractional_type: Union[str, highdicom.seg.SegmentationFractionalTypeValues], optional
            Type of fractional segmentation that indicates how pixel data
            should be interpreted
        max_fractional_value: int, optional
            Maximum value that indicates probability or occupancy of 1 that
            a pixel represents a given segment
        content_description: str, optional
            Description of the segmentation
        content_creator_name: str, optional
            Name of the creator of the segmentation
        transfer_syntax_uid: str, optional
            UID of transfer syntax that should be used for encoding of
            data elements. The following lossless compressed transfer syntaxes
            are supported for encapsulated format encoding in case of
            FRACTIONAL segmentation type:
            RLE Lossless (``"1.2.840.10008.1.2.5"``) and
            JPEG 2000 Lossless (``"1.2.840.10008.1.2.4.90"``).
        pixel_measures: PixelMeasures, optional
            Physical spacing of image pixels in `pixel_array`.
            If ``None``, it will be assumed that the segmentation image has the
            same pixel measures as the source image(s).
        plane_orientation: highdicom.PlaneOrientationSequence, optional
            Orientation of planes in `pixel_array` relative to axes of
            three-dimensional patient or slide coordinate space.
            If ``None``, it will be assumed that the segmentation image as the
            same plane orientation as the source image(s).
        plane_positions: Sequence[highdicom.PlanePositionSequence], optional
            Position of each plane in `pixel_array` in the three-dimensional
            patient or slide coordinate space.
            If ``None``, it will be assumed that the segmentation image has the
            same plane position as the source image(s). However, this will only
            work when the first dimension of `pixel_array` matches the number
            of frames in `source_images` (in case of multi-frame source images)
            or the number of `source_images` (in case of single-frame source
            images).
        **kwargs: Any, optional
            Additional keyword arguments that will be passed to the constructor
            of `highdicom.base.SOPClass`

        Raises
        ------
        ValueError
            When
                * Length of `source_images` is zero.
                * Items of `source_images` are not all part of the same study
                  and series.
                * Items of `source_images` have different number of rows and
                  columns.
                * Length of `plane_positions` does not match number of segments
                  encoded in `pixel_array`.
                * Length of `plane_positions` does not match number of 2D planes
                  in `pixel_array` (size of first array dimension).

        Note
        ----
        The assumption is made that segments in `pixel_array` are defined in
        the same frame of reference as `source_images`.


        """  # noqa
        if len(source_images) == 0:
            raise ValueError('At least one source image is required.')
        self._source_images = source_images

        uniqueness_criteria = set(
            (
                image.StudyInstanceUID,
                image.SeriesInstanceUID,
                image.Rows,
                image.Columns,
            )
            for image in self._source_images
        )
        if len(uniqueness_criteria) > 1:
            raise ValueError(
                'Source images must all be part of the same series and must '
                'have the same image dimensions (number of rows/columns).'
            )

        src_img = self._source_images[0]
        is_multiframe = hasattr(src_img, 'NumberOfFrames')
        if is_multiframe and len(self._source_images) > 1:
            raise ValueError(
                'Only one source image should be provided in case images '
                'are multi-frame images.'
            )
        supported_transfer_syntaxes = {
            ImplicitVRLittleEndian,
            ExplicitVRLittleEndian,
            JPEG2000Lossless,
            RLELossless,
        }
        if transfer_syntax_uid not in supported_transfer_syntaxes:
            raise ValueError(
                'Transfer syntax "{}" is not supported'.format(
                    transfer_syntax_uid
                )
            )

        if pixel_array.ndim == 2:
            pixel_array = pixel_array[np.newaxis, ...]

        super().__init__(
            study_instance_uid=src_img.StudyInstanceUID,
            series_instance_uid=series_instance_uid,
            series_number=series_number,
            sop_instance_uid=sop_instance_uid,
            instance_number=instance_number,
            sop_class_uid='1.2.840.10008.5.1.4.1.1.66.4',
            manufacturer=manufacturer,
            modality='SEG',
            transfer_syntax_uid=transfer_syntax_uid,
            patient_id=src_img.PatientID,
            patient_name=src_img.PatientName,
            patient_birth_date=src_img.PatientBirthDate,
            patient_sex=src_img.PatientSex,
            accession_number=src_img.AccessionNumber,
            study_id=src_img.StudyID,
            study_date=src_img.StudyDate,
            study_time=src_img.StudyTime,
            referring_physician_name=getattr(
                src_img, 'ReferringPhysicianName', None
            ),
            **kwargs
        )

        # Using Container Type Code Sequence attribute would be more elegant,
        # but unfortunately it is a type 2 attribute.
        if (hasattr(src_img, 'ImageOrientationSlide') or
                hasattr(src_img, 'ImageCenterPointCoordinatesSequence')):
            self._coordinate_system = CoordinateSystemNames.SLIDE
        else:
            self._coordinate_system = CoordinateSystemNames.PATIENT

        # Frame of Reference
        self.FrameOfReferenceUID = src_img.FrameOfReferenceUID
        self.PositionReferenceIndicator = getattr(
            src_img,
            'PositionReferenceIndicator',
            None
        )

        # (Enhanced) General Equipment
        self.DeviceSerialNumber = device_serial_number
        self.ManufacturerModelName = manufacturer_model_name
        self.SoftwareVersions = software_versions

        # General Reference
        self.SourceImageSequence: List[Dataset] = []
        referenced_series: Dict[str, List[Dataset]] = defaultdict(list)
        for s_img in self._source_images:
            ref = Dataset()
            ref.ReferencedSOPClassUID = s_img.SOPClassUID
            ref.ReferencedSOPInstanceUID = s_img.SOPInstanceUID
            self.SourceImageSequence.append(ref)
            referenced_series[s_img.SeriesInstanceUID].append(ref)

        # Common Instance Reference
        self.ReferencedSeriesSequence: List[Dataset] = []
        for series_instance_uid, referenced_images in referenced_series.items():
            ref = Dataset()
            ref.SeriesInstanceUID = series_instance_uid
            ref.ReferencedInstanceSequence = referenced_images
            self.ReferencedSeriesSequence.append(ref)

        # Image Pixel
        self.Rows = pixel_array.shape[1]
        self.Columns = pixel_array.shape[2]

        # Segmentation Image
        self.ImageType = ['DERIVED', 'PRIMARY']
        self.SamplesPerPixel = 1
        self.PhotometricInterpretation = 'MONOCHROME2'
        self.PixelRepresentation = 0
        self.ContentLabel = 'ISO_IR 192'  # UTF-8
        self.ContentDescription = content_description
        self.ContentCreatorName = content_creator_name

        segmentation_type = SegmentationTypeValues(segmentation_type)
        self.SegmentationType = segmentation_type.value
        if self.SegmentationType == SegmentationTypeValues.BINARY.value:
            self.BitsAllocated = 1
            self.HighBit = 0
            if self.file_meta.TransferSyntaxUID.is_encapsulated:
                raise ValueError(
                    'The chosen transfer syntax '
                    f'{self.file_meta.TransferSyntaxUID} '
                    'is not compatible with the BINARY segmentation type'
                )
        elif self.SegmentationType == SegmentationTypeValues.FRACTIONAL.value:
            self.BitsAllocated = 8
            self.HighBit = 7
            segmentation_fractional_type = SegmentationFractionalTypeValues(
                fractional_type
            )
            self.SegmentationFractionalType = segmentation_fractional_type.value
            if max_fractional_value > 2**8:
                raise ValueError(
                    'Maximum fractional value must not exceed image bit depth.'
                )
            self.MaximumFractionalValue = max_fractional_value
        else:
            raise ValueError(
                'Unknown segmentation type "{}"'.format(segmentation_type)
            )

        self.BitsStored = self.BitsAllocated
        self.LossyImageCompression = getattr(
            src_img,
            'LossyImageCompression',
            '00'
        )
        if self.LossyImageCompression == '01':
            self.LossyImageCompressionRatio = \
                src_img.LossyImageCompressionRatio
            self.LossyImageCompressionMethod = \
                src_img.LossyImageCompressionMethod

        # will be updated by "add_segments()"
        self.SegmentSequence: List[Dataset] = []

        # Multi-Frame Functional Groups and Multi-Frame Dimensions
        shared_func_groups = Dataset()
        if pixel_measures is None:
            if is_multiframe:
                src_shared_fg = src_img.SharedFunctionalGroupsSequence[0]
                pixel_measures = src_shared_fg.PixelMeasuresSequence
            else:
                pixel_measures = PixelMeasuresSequence(
                    pixel_spacing=src_img.PixelSpacing,
                    slice_thickness=src_img.SliceThickness,
                    spacing_between_slices=src_img.get(
                        'SpacingBetweenSlices',
                        None
                    )
                )
            # TODO: ensure derived segmentation image and original image have
            # same physical dimensions
            # seg_row_dim = self.Rows * pixel_measures[0].PixelSpacing[0]
            # seg_col_dim = self.Columns * pixel_measures[0].PixelSpacing[1]
            # src_row_dim = src_img.Rows

        if is_multiframe:
            if self._coordinate_system == CoordinateSystemNames.SLIDE:
                source_plane_orientation = PlaneOrientationSequence(
                    coordinate_system=self._coordinate_system,
                    image_orientation=src_img.ImageOrientationSlide
                )
            else:
                src_sfg = src_img.SharedFunctionalGroupsSequence[0]
                source_plane_orientation = src_sfg.PlaneOrientationSequence
        else:
            source_plane_orientation = PlaneOrientationSequence(
                coordinate_system=self._coordinate_system,
                image_orientation=src_img.ImageOrientationPatient
            )
        if plane_orientation is None:
            plane_orientation = source_plane_orientation
        self._plane_orientation = plane_orientation
        self._source_plane_orientation = source_plane_orientation

        self.DimensionIndexSequence = DimensionIndexSequence(
            coordinate_system=self._coordinate_system
        )
        dimension_organization = Dataset()
        dimension_organization.DimensionOrganizationUID = \
            self.DimensionIndexSequence[0].DimensionOrganizationUID
        self.DimensionOrganizationSequence = [dimension_organization]

        shared_func_groups.PixelMeasuresSequence = pixel_measures
        shared_func_groups.PlaneOrientationSequence = plane_orientation
        self.SharedFunctionalGroupsSequence = [shared_func_groups]

        # NOTE: Information about individual frames will be updated by the
        # "add_segments()" method upon addition of segmentation bitplanes.
        self.NumberOfFrames = 0
        self.PerFrameFunctionalGroupsSequence: List[Dataset] = []

        self._segment_inventory: Set[int] = set()
        self.PixelData = b''
        self.add_segments(
            pixel_array=pixel_array,
            segment_descriptions=segment_descriptions,
            plane_positions=plane_positions
        )

        self.copy_specimen_information(src_img)
        self.copy_patient_and_study_information(src_img)

    def add_segments(
        self,
        pixel_array: np.ndarray,
        segment_descriptions: Sequence[SegmentDescription],
        plane_positions: Optional[Sequence[PlanePositionSequence]] = None
    ) -> None:
        """Adds one or more segments to the segmentation image.

        Parameters
        ----------
        pixel_array: numpy.ndarray
            Array of segmentation pixel data of boolean, unsigned integer or
            floating point data type representing a mask image. If `pixel_array`
            is a floating-point array or a binary array (containing only the
            values ``True`` and ``False`` or ``0`` and ``1``), the segment
            number used to encode the segment is taken from
            `segment_descriptions`.
            Otherwise, if `pixel_array` contains multiple integer values, each
            value is treated as a different segment whose segment number is
            that integer value. In this case, all segments found in the array
            must be described in `segment_descriptions`. Note that this is
            valid for both ``"BINARY"`` and ``"FRACTIONAL"`` segmentations.
            For ``"FRACTIONAL"`` segmentations, values either encode the
            probability of a given pixel belonging to a segment
            (if `fractional_type` is ``"PROBABILITY"``)
            or the extent to which a segment occupies the pixel
            (if `fractional_type` is ``"OCCUPANCY"``).
            When `pixel_array` has a floating point data type, only one segment
            can be encoded. Additional segments can be subsequently
            added to the `Segmentation` instance using the ``add_segments()``
            method.
            If `pixel_array` represents a 3D image, the first dimension
            represents individual 2D planes and these planes must be ordered
            based on their position in the three-dimensional patient
            coordinate system (first along the X axis, second along the Y axis,
            and third along the Z axis).
            If `pixel_array` represents a tiled 2D image, the first dimension
            represents individual 2D tiles (for one channel and z-stack) and
            these tiles must be ordered based on their position in the tiled
            total pixel matrix (first along the row dimension and second along
            the column dimension, which are defined in the three-dimensional
            slide coordinate system by the direction cosines encoded by the
            *Image Orientation (Slide)* attribute).
        segment_descriptions: Sequence[highdicom.seg.SegmentDescription]
            Description of each segment encoded in `pixel_array`. In the case of
            pixel arrays with multiple integer values, the segment description
            with the corresponding segment number is used to describe each
            segment.
        plane_positions: Sequence[highdicom.PlanePositionSequence], optional
            Position of each plane in `pixel_array` relative to the
            three-dimensional patient or slide coordinate system.

        Raises
        ------
        ValueError
            When
                - The pixel array is not 2D or 3D numpy array
                - The shape of the pixel array does not match the source images
                - The numbering of the segment descriptions is not
                  monotonically increasing by 1
                - The numbering of the segment descriptions does
                  not begin at 1 (for the first segments added to the instance)
                  or at one greater than the last added segment (for
                  subsequent segments)
                - One or more segments already exist within the
                  segmentation instance
                - The segmentation is binary and the pixel array contains
                  integer values that belong to segments that are not described
                  in the segment descriptions
                - The segmentation is binary and pixel array has floating point
                  values not equal to 0.0 or 1.0
                - The segmentation is fractional and pixel array has floating
                  point values outside the range 0.0 to 1.0
                - The segmentation is fractional and pixel array has floating
                  point values outside the range 0.0 to 1.0
                - Plane positions are provided but the length of the array
                  does not match the number of frames in the pixel array
        TypeError
            When the dtype of the pixel array is invalid


        Note
        ----
        Segments must be sorted by segment number in ascending order and
        increase by 1.  Additionally, the first segment description must have a
        segment number one greater than the segment number of the last segment
        added to the segmentation, or 1 if this is the first segment added.

        In case `segmentation_type` is ``"BINARY"``, the number of items in
        `segment_descriptions` must be greater than or equal to the number of
        unique positive pixel values in `pixel_array`. It is possible for some
        segments described in `segment_descriptions` not to appear in the
        `pixel_array`. In case `segmentation_type` is ``"FRACTIONAL"``, only
        one segment can be encoded by `pixel_array` and hence only one item is
        permitted in `segment_descriptions`.

        """  # noqa
        if self._source_images is None:
            raise AttributeError(
                'Further segments may not be added to Segmentation objects '
                'created from existing datasets.'
            )
        if pixel_array.ndim == 2:
            pixel_array = pixel_array[np.newaxis, ...]
        if pixel_array.ndim != 3:
            raise ValueError('Pixel array must be a 2D or 3D array.')

        if pixel_array.shape[1:3] != (self.Rows, self.Columns):
            raise ValueError(
                'Pixel array representing segments has the wrong number of '
                'rows and columns.'
            )

        # Determine the expected starting number of the segments to ensure
        # they will be continuous with existing segments
        if self._segment_inventory:
            # Next segment number is one greater than the largest existing
            # segment number
            seg_num_start = max(self._segment_inventory) + 1
        else:
            # No existing segments so start at 1
            seg_num_start = 1

        # Check segment numbers
        # Check the existing descriptions
        described_segment_numbers = np.array([
            int(item.SegmentNumber)
            for item in segment_descriptions
        ])
        # Check segment numbers in the segment descriptions are
        # monotonically increasing by 1
        if not (np.diff(described_segment_numbers) == 1).all():
            raise ValueError(
                'Segment descriptions must be sorted by segment number '
                'and monotonically increasing by 1.'
            )
        if described_segment_numbers[0] != seg_num_start:
            if seg_num_start == 1:
                msg = (
                    'Segment descriptions should be numbered starting '
                    f'from 1. Found {described_segment_numbers[0]}. '
                )
            else:
                msg = (
                    'Segment descriptions should be numbered to '
                    'continue from existing segments. Expected the first '
                    f'segment to be numbered {seg_num_start} but found '
                    f'{described_segment_numbers[0]}.'
                )
            raise ValueError(msg)

        if pixel_array.dtype in (np.bool_, np.uint8, np.uint16):
            segments_present = np.unique(
                pixel_array[pixel_array > 0].astype(np.uint16)
            )

            # Special case where the mask is binary and there is a single
            # segment description. Mark the positive segment with
            # the correct segment number
            if (np.array_equal(segments_present, np.array([1])) and
                    len(segment_descriptions) == 1):
                pixel_array = pixel_array.astype(np.uint8)
                pixel_array *= described_segment_numbers.item()

            # Otherwise, the pixel values in the pixel array must all belong to
            # a described segment
            else:
                if not np.all(
                        np.in1d(segments_present, described_segment_numbers)
                    ):
                    raise ValueError(
                        'Pixel array contains segments that lack '
                        'descriptions.'
                    )

        elif (pixel_array.dtype in (np.float_, np.float32, np.float64)):
            unique_values = np.unique(pixel_array)
            if np.min(unique_values) < 0.0 or np.max(unique_values) > 1.0:
                raise ValueError(
                    'Floating point pixel array values must be in the '
                    'range [0, 1].'
                )
            if len(segment_descriptions) != 1:
                raise ValueError(
                    'When providing a float-valued pixel array, provide only '
                    'a single segment description'
                )
            if self.SegmentationType == SegmentationTypeValues.BINARY.value:
                non_boolean_values = np.logical_and(
                    unique_values > 0.0,
                    unique_values < 1.0
                )
                if np.any(non_boolean_values):
                    raise ValueError(
                        'Floating point pixel array values must be either '
                        '0.0 or 1.0 in case of BINARY segmentation type.'
                    )
                pixel_array = pixel_array.astype(np.bool_)
        else:
            raise TypeError('Pixel array has an invalid data type.')

        # Check that the new segments do not already exist
        if len(set(described_segment_numbers) & self._segment_inventory) > 0:
            raise ValueError('Segment with given segment number already exists')

        # Set the optional tag value SegmentsOverlapValues to NO to indicate
        # that the segments do not overlap. We can know this for sure if it's
        # the first segment (or set of segments) to be added because they are
        # contained within a single pixel array.
        if len(self._segment_inventory) == 0:
            self.SegmentsOverlap = SegmentsOverlapValues.NO.value
        else:
            # If this is not the first set of segments to be added, we cannot
            # be sure whether there is overlap with the existing segments
            self.SegmentsOverlap = SegmentsOverlapValues.UNDEFINED.value

        src_image = self._source_images[0]
        is_multiframe = hasattr(src_image, 'NumberOfFrames')
        if is_multiframe:
            source_plane_positions = \
                self.DimensionIndexSequence.get_plane_positions_of_image(
                    src_image
                )
        else:
            source_plane_positions = \
                self.DimensionIndexSequence.get_plane_positions_of_series(
                    self._source_images
                )

        if plane_positions is None:
            if pixel_array.shape[0] != len(source_plane_positions):
                if is_multiframe:
                    raise ValueError(
                        'Number of frames in pixel array does not match number '
                        ' of frames in source image.'
                    )
                else:
                    raise ValueError(
                        'Number of frames in pixel array does not match number '
                        'of source images.'
                    )
            plane_positions = source_plane_positions
        else:
            if pixel_array.shape[0] != len(plane_positions):
                raise ValueError(
                    'Number of pixel array planes does not match number of '
                    'provided plane positions.'
                )

        plane_position_values, plane_sort_index = \
            self.DimensionIndexSequence.get_index_values(plane_positions)

        are_spatial_locations_preserved = (
            all(
                plane_positions[i] == source_plane_positions[i]
                for i in range(len(plane_positions))
            ) and
            self._plane_orientation == self._source_plane_orientation
        )

        # Get unique values of attributes in the Plane Position Sequence or
        # Plane Position Slide Sequence, which define the position of the plane
        # with respect to the three dimensional patient or slide coordinate
        # system, respectively. These can subsequently be used to look up the
        # relative position of a plane relative to the indexed dimension.
        dimension_position_values = [
            np.unique(plane_position_values[:, index], axis=0)
            for index in range(plane_position_values.shape[1])
        ]

        # In certain circumstances, we can add new pixels without unpacking the
        # previous ones, which is more efficient. This can be done when using
        # non-encapsulated transfer syntaxes when there is no padding required
        # for each frame to be a multiple of 8 bits.
        framewise_encoding = False
        is_encaps = self.file_meta.TransferSyntaxUID.is_encapsulated
        if not is_encaps:
            if self.SegmentationType == SegmentationTypeValues.FRACTIONAL.value:
                framewise_encoding = True
            elif self.SegmentationType == SegmentationTypeValues.BINARY.value:
                # Framewise encoding can only be used if there is no padding
                # This requires the number of pixels in each frame to be
                # multiple of 8
                if (self.Rows * self.Columns * self.SamplesPerPixel) % 8 == 0:
                    framewise_encoding = True
                else:
                    logger.warning(
                        'pixel data needs to be re-encoded for binary '
                        'bitpacking - consider using FRACTIONAL instead of '
                        'BINARY segmentation type'
                    )

        if framewise_encoding:
            # Before adding new pixel data, remove trailing null padding byte
            if len(self.PixelData) == get_expected_length(self) + 1:
                self.PixelData = self.PixelData[:-1]
        else:
            # In the case of encapsulated transfer syntaxes, we will accumulate
            # a list of encoded frames to re-encapsulate at the end
            if is_encaps:
                if hasattr(self, 'PixelData') and len(self.PixelData) > 0:
                    # Undo the encapsulation but not the encoding within each
                    # frame
                    full_frames_list = decode_data_sequence(self.PixelData)
                else:
                    full_frames_list = []
            else:
                if hasattr(self, 'PixelData') and len(self.PixelData) > 0:
                    full_pixel_array = self.pixel_array.flatten()
                else:
                    full_pixel_array = np.array([], np.bool_)

        for i, segment_number in enumerate(described_segment_numbers):
            if pixel_array.dtype in (np.float_, np.float32, np.float64):
                # Floating-point numbers must be mapped to 8-bit integers in
                # the range [0, max_fractional_value].
                planes = np.around(
                    pixel_array * float(self.MaximumFractionalValue)
                )
                planes = planes.astype(np.uint8)
            elif pixel_array.dtype in (np.uint8, np.uint16):
                # Labeled masks must be converted to binary masks.
                planes = np.zeros(pixel_array.shape, dtype=np.bool_)
                planes[pixel_array == segment_number] = True
            elif pixel_array.dtype == np.bool_:
                planes = pixel_array
            else:
                raise TypeError('Pixel array has an invalid data type.')

            contained_plane_index = []
            for j in plane_sort_index:
                if np.sum(planes[j]) == 0:
                    logger.info(
                        'skip empty plane {} of segment #{}'.format(
                            j, segment_number
                        )
                    )
                    continue
                contained_plane_index.append(j)
                logger.info(
                    'add plane #{} for segment #{}'.format(
                        j, segment_number
                    )
                )

                pffp_item = Dataset()
                frame_content_item = Dataset()
                frame_content_item.DimensionIndexValues = [segment_number]

                # Look up the position of the plane relative to the indexed
                # dimension.
                try:
                    if self._coordinate_system == CoordinateSystemNames.SLIDE:
                        index_values = [
                            np.where(
                                (dimension_position_values[idx] == pos)
                            )[0][0] + 1
                            for idx, pos in enumerate(plane_position_values[j])
                        ]
                    else:
                        # In case of the patient coordinate system, the
                        # value of the attribute the Dimension Index Sequence
                        # points to (Image Position Patient) has a value
                        # multiplicity greater than one.
                        index_values = [
                            np.where(
                                (dimension_position_values[idx] == pos).all(
                                    axis=1
                                )
                            )[0][0] + 1
                            for idx, pos in enumerate(plane_position_values[j])
                        ]
                except IndexError as error:
                    raise IndexError(
                        'Could not determine position of plane #{} in '
                        'three dimensional coordinate system based on '
                        'dimension index values: {}'.format(j, error)
                    )
                frame_content_item.DimensionIndexValues.extend(index_values)
                pffp_item.FrameContentSequence = [frame_content_item]
                if self._coordinate_system == CoordinateSystemNames.SLIDE:
                    pffp_item.PlanePositionSlideSequence = plane_positions[j]
                else:
                    pffp_item.PlanePositionSequence = plane_positions[j]

                # Determining the source images that map to the frame is not
                # always trivial. Since DerivationImageSequence is a type 2
                # attribute, we leave its value empty.
                pffp_item.DerivationImageSequence = []

                if are_spatial_locations_preserved:
                    derivation_image_item = Dataset()
                    derivation_code = codes.cid7203.Segmentation
                    derivation_image_item.DerivationCodeSequence = [
                        CodedConcept(
                            derivation_code.value,
                            derivation_code.scheme_designator,
                            derivation_code.meaning,
                            derivation_code.scheme_version
                        ),
                    ]

                    derivation_src_img_item = Dataset()
                    if len(plane_sort_index) > len(self._source_images):
                        # A single multi-frame source image
                        src_img_item = self.SourceImageSequence[0]
                        # Frame numbers are one-based
                        derivation_src_img_item.ReferencedFrameNumber = j + 1
                    else:
                        # Multiple single-frame source images
                        src_img_item = self.SourceImageSequence[j]
                    derivation_src_img_item.ReferencedSOPClassUID = \
                        src_img_item.ReferencedSOPClassUID
                    derivation_src_img_item.ReferencedSOPInstanceUID = \
                        src_img_item.ReferencedSOPInstanceUID
                    purpose_code = \
                        codes.cid7202.SourceImageForImageProcessingOperation
                    derivation_src_img_item.PurposeOfReferenceCodeSequence = [
                        CodedConcept(
                            purpose_code.value,
                            purpose_code.scheme_designator,
                            purpose_code.meaning,
                            purpose_code.scheme_version
                        ),
                    ]
                    derivation_src_img_item.SpatialLocationsPreserved = 'YES'
                    derivation_image_item.SourceImageSequence = [
                        derivation_src_img_item,
                    ]
                    pffp_item.DerivationImageSequence.append(
                        derivation_image_item
                    )
                else:
                    logger.warning('spatial locations not preserved')

                identification = Dataset()
                identification.ReferencedSegmentNumber = segment_number
                pffp_item.SegmentIdentificationSequence = [
                    identification,
                ]
                self.PerFrameFunctionalGroupsSequence.append(pffp_item)
                self.NumberOfFrames += 1

            if framewise_encoding:
                # Straightforward concatenation of the binary data
                self.PixelData += self._encode_pixels(
                    planes[contained_plane_index]
                )
            else:
                if is_encaps:
                    # Encode this frame and add to the list for encapsulation
                    # at the end
                    for f in contained_plane_index:
                        full_frames_list.append(self._encode_pixels(planes[f]))
                else:
                    # Concatenate the 1D array for re-encoding at the end
                    full_pixel_array = np.concatenate([
                        full_pixel_array,
                        planes[contained_plane_index].flatten()
                    ])

            # In case of a tiled Total Pixel Matrix pixel data for the same
            # segment may be added.
            if segment_number not in self._segment_inventory:
                self.SegmentSequence.append(segment_descriptions[i])
            self._segment_inventory.add(segment_number)

        # Re-encode the whole pixel array at once if necessary
        if not framewise_encoding:
            if is_encaps:
                self.PixelData = encapsulate(full_frames_list)
            else:
                self.PixelData = self._encode_pixels(full_pixel_array)

        # Add back the null trailing byte if required
        if len(self.PixelData) % 2 == 1:
            self.PixelData += b'0'

        self._build_luts()

    def _encode_pixels(self, planes: np.ndarray) -> bytes:
        """Encodes pixel planes.

        Parameters
        ----------
        planes: numpy.ndarray
            Array representing one or more segmentation image planes.
            For encapsulated transfer syntaxes, only a single frame may be
            processed. For other transfer syntaxes, multiple planes in a 3D
            array may be processed.

        Returns
        -------
        bytes
            Encoded pixels

        Raises
        ------
        ValueError
            If multiple frames are passed when using an encapsulated
            transfer syntax.

        """
        if self.file_meta.TransferSyntaxUID.is_encapsulated:
            # Check that only a single plane was passed
            if planes.ndim == 3:
                if planes.shape[0] == 1:
                    planes = planes[0, ...]
                else:
                    raise ValueError(
                        'Only single frame can be encoded at at time '
                        'in case of encapsulated format encoding.'
                    )
            return encode_frame(
                planes,
                transfer_syntax_uid=self.file_meta.TransferSyntaxUID,
                bits_allocated=self.BitsAllocated,
                bits_stored=self.BitsStored,
                photometric_interpretation=self.PhotometricInterpretation,
                pixel_representation=self.PixelRepresentation
            )
        else:
            # The array may represent more than one frame item.
            if self.SegmentationType == SegmentationTypeValues.BINARY.value:
                return pack_bits(planes.flatten())
            else:
                return planes.flatten().tobytes()

    @classmethod
    def from_dataset(cls, dataset: Dataset) -> 'Segmentation':
        """Create a Segmentation object from an existing pydicom dataset.

        Parameters
        ----------
        dataset: pydicom.dataset.Dataset
            Pydicom dataset representing a SEG image.

        Returns
        -------
        highdicom.seg.Segmentation
            Representation of the supplied dataset as a highdicom
            Segmentation.

        """
        if not isinstance(dataset, Dataset):
            raise TypeError(
                'Dataset must be of type pydicom.dataset.Dataset.'
            )
        # Checks on integrity of input dataset
        if dataset.SOPClassUID != '1.2.840.10008.5.1.4.1.1.66.4':
            raise ValueError(
                'Dataset is not a Segmentation.'
            )
        seg = deepcopy(dataset)
        seg.__class__ = cls

        seg._source_images = None
        seg._source_plane_orientation = None
        sf_groups = seg.SharedFunctionalGroupsSequence[0]
        plane_ori_seq = sf_groups.PlaneOrientationSequence[0]
        if hasattr(plane_ori_seq, 'ImageOrientationSlide'):
            seg._coordinate_system = CoordinateSystemNames.SLIDE
            seg._plane_orientation = plane_ori_seq.ImageOrientationSlide
        elif hasattr(plane_ori_seq, 'ImageOrientationPatient'):
            seg._coordinate_system = CoordinateSystemNames.PATIENT
            seg._plane_orientation = plane_ori_seq.ImageOrientationPatient
        else:
            raise DicomAttributeError(
                'Expected Plane Orientation Sequence to have either '
                'ImageOrientationSlide or ImageOrientationPatient '
                'attribute.'
            )

        for i, segment in enumerate(seg.SegmentSequence, 1):
            if segment.SegmentNumber != i:
                raise DicomAttributeError(
                    'Segments are expected to start at 1 and be consecutive '
                    'integers.'
                )

        seg._segment_inventory = {
            s.SegmentNumber for s in seg.SegmentSequence
        }

        # Convert contained items to highdicom types
        # Segment descriptions
        seg.SegmentSequence = [
            SegmentDescription.from_dataset(ds) for ds in seg.SegmentSequence
        ]

        # Shared functional group elements
        if hasattr(sf_groups, 'PlanePositionSequence'):
            plane_pos = PlanePositionSequence.from_sequence(
                sf_groups.PlanePositionSequence
            )
            sf_groups.PlanePositionSequence = plane_pos
        if hasattr(sf_groups, 'PlaneOrientationSequence'):
            plane_ori = PlaneOrientationSequence.from_sequence(
                sf_groups.PlaneOrientationSequence
            )
            sf_groups.PlaneOrientationSequence = plane_ori
        if hasattr(sf_groups, 'PixelMeasuresSequence'):
            pixel_measures = PixelMeasuresSequence.from_sequence(
                sf_groups.PixelMeasuresSequence
            )
            sf_groups.PixelMeasuresSequence = pixel_measures

        # Per-frame functional group items
        for pffg_item in seg.PerFrameFunctionalGroupsSequence:
            if hasattr(pffg_item, 'PlanePositionSequence'):
                plane_pos = PlanePositionSequence.from_sequence(
                    pffg_item.PlanePositionSequence
                )
                pffg_item.PlanePositionSequence = plane_pos
            if hasattr(pffg_item, 'PlaneOrientationSequence'):
                plane_ori = PlaneOrientationSequence.from_sequence(
                    pffg_item.PlaneOrientationSequence
                )
                pffg_item.PlaneOrientationSequence = plane_ori
            if hasattr(pffg_item, 'PixelMeasuresSequence'):
                pixel_measures = PixelMeasuresSequence.from_sequence(
                    pffg_item.PixelMeasuresSequence
                )
                pffg_item.PixelMeasuresSequence = pixel_measures

        seg._build_luts()

        return seg

    def _build_ref_instance_lut(self) -> None:
        # Map sop uid to tuple (study uid, series uid, sop uid)
        self._ref_ins_lut = OrderedDict()
        if hasattr(self, 'ReferencedSeriesSequence'):
            for ref_series in self.ReferencedSeriesSequence:
                for ref_ins in ref_series.ReferencedInstanceSequence:
                    self._ref_ins_lut[ref_ins.ReferencedSOPInstanceUID] = (
                        self.StudyInstanceUID,
                        ref_series.SeriesInstanceUID,
                        ref_ins.ReferencedSOPInstanceUID
                    )
        other_studies_kw = 'StudiesContainingOtherReferencedInstancesSequence'
        if hasattr(self, other_studies_kw):
            for ref_study in getattr(self, other_studies_kw):
                for ref_series in ref_study.ReferencedSeriesSequence:
                    for ref_ins in ref_series.ReferencedInstanceSequence:
                        self._ref_ins_lut[ref_ins.ReferencedSOPInstanceUID] = (
                            ref_study.StudyInstanceUID,
                            ref_series.SeriesInstanceUID,
                            ref_ins.ReferencedSOPInstanceUID
                        )

        self._source_sop_instance_uids = np.array(
            list(self._ref_ins_lut.keys())
        )

    def _build_luts(self) -> None:

        self._build_ref_instance_lut()

        segnum_col_data = []
        source_instance_col_data = []
        source_frame_col_data = []

        # Create a list of source images and check for spatial locations
        # preserved and that there is a single source frame per seg frame
        locations_preserved = []
        self._single_source_frame_per_seg_frame = True
        for frame_item in self.PerFrameFunctionalGroupsSequence:
            # Get segment number for this frame
            seg_id_seg = frame_item.SegmentIdentificationSequence[0]
            seg_num = seg_id_seg.ReferencedSegmentNumber
            segnum_col_data.append(int(seg_num))

            frame_source_instances = []
            frame_source_frames = []
            for der_im in frame_item.DerivationImageSequence:
                for src_im in der_im.SourceImageSequence:
                    frame_source_instances.append(
                        src_im.ReferencedSOPInstanceUID
                    )
                    if hasattr(src_im, 'SpatialLocationsPreserved'):
                        locations_preserved.append(
                            SpatialLocationsPreservedValues(
                                src_im.SpatialLocationsPreserved
                            )
                        )
                    else:
                        locations_preserved.append(
                            SpatialLocationsPreservedValues.UNKNOWN
                        )

                    if hasattr(src_im, 'ReferencedFrameNumber'):
                        if isinstance(
                            src_im.ReferencedFrameNumber,
                            MultiValue
                        ):
                            frame_source_frames.extend(
                                [
                                    int(f)
                                    for f in src_im.ReferencedFrameNumber
                                ]
                            )
                        else:
                            frame_source_frames.append(
                                int(src_im.ReferencedFrameNumber)
                            )
                    else:
                        frame_source_frames.append(_NO_FRAME_REF_VALUE)

            if (
                len(set(frame_source_instances)) != 1 or
                len(set(frame_source_frames)) != 1
            ):
                self._single_source_frame_per_seg_frame = False
            else:
                ref_instance_uid = frame_source_instances[0]
                if ref_instance_uid not in self._source_sop_instance_uids:
                    raise DicomAttributeError(
                        f'SOP instance {ref_instance_uid} referenced in the '
                        'source image sequence is not included in the '
                        'Referenced Series Sequence or Studies Containing '
                        'Other Referenced Instances Sequence.'
                    )
                src_uid_ind = self._get_src_uid_index(frame_source_instances[0])
                source_instance_col_data.append(src_uid_ind)
                source_frame_col_data.append(frame_source_frames[0])

        # Summarise
        if any(
            v == SpatialLocationsPreservedValues.NO
            for v in locations_preserved
        ):
            Type = Optional[SpatialLocationsPreservedValues]
            self._locations_preserved: Type = SpatialLocationsPreservedValues.NO
        elif all(
            v == SpatialLocationsPreservedValues.YES
            for v in locations_preserved
        ):
            self._locations_preserved = SpatialLocationsPreservedValues.YES
        else:
            self._locations_preserved = None

        # Frame LUT is a 2D numpy array with three columns
        # Row i represents frame i of the segmentation dataset
        # The three columns represent the segment number, the source uid
        # index in the source_sop_instance_uids list, and the source frame
        # number (if applicable)
        # This allows for fairly efficient querying by any of the three
        # variables: seg frame number, source instance/frame number, segment
        # number
        if self._single_source_frame_per_seg_frame:
            self._frame_lut = np.array([
                segnum_col_data,
                source_instance_col_data,
                source_frame_col_data
            ]).T
            self._lut_seg_col = 0
            self._lut_src_instance_col = 1
            self._lut_src_frame_col = 2

    @property
    def segmentation_type(self) -> SegmentationTypeValues:
        """highdicom.seg.SegmentationTypeValues: Segmentation type."""
        return SegmentationTypeValues(self.SegmentationType)

    @property
    def segmentation_fractional_type(
        self
    ) -> Optional[SegmentationFractionalTypeValues]:
        """
        highdicom.seg.SegmentationFractionalTypeValues:
            Segmentation fractional type.

        """
        if not hasattr(self, 'SegmentationFractionalType'):
            return None
        return SegmentationFractionalTypeValues(
            self.SegmentationFractionalType
        )

    def iter_segments(self):
        return iter_segments(self)

    @property
    def number_of_segments(self) -> int:
        """int: The number of segments in this SEG image."""
        return len(self.SegmentSequence)

    @property
    def segment_numbers(self) -> range:
        """range: The segment numbers present in the SEG image as a range."""
        return range(1, self.number_of_segments + 1)

    def get_segment_description(
        self,
        segment_number: int
    ) -> SegmentDescription:
        """Get segment description for a segment.

        Parameters
        ----------
        segment_number: int
            Segment number for the segment, as a 1-based index.

        Returns
        -------
        highdicom.seg.SegmentDescription
            Description of the given segment.

        """
        if segment_number < 1 or segment_number > self.number_of_segments:
            raise IndexError(
                f'{segment_number} is an invalid segment number for this '
                'dataset.'
            )
        return self.SegmentSequence[segment_number - 1]

    def search_for_segments(
        self,
        segment_label: Optional[str] = None,
        segmented_property_category: Optional[Union[Code, CodedConcept]] = None,
        segmented_property_type: Optional[Union[Code, CodedConcept]] = None,
        algorithm_type: Optional[Union[SegmentAlgorithmTypeValues, str]] = None,
        tracking_uid: Optional[str] = None,
        tracking_id: Optional[str] = None,
    ) -> List[int]:
        """Get a list of segment numbers matching provided criteria.

        Any number of optional filters may be provided. A segment must match
        all provided filters to be included in the returned list.

        Parameters
        ----------
        segment_label: Optional[str]
            Segment label filter to apply.
        segmented_property_category: Optional[Union[Code, CodedConcept]]
            Segmented property category filter to apply.
        segmented_property_type: Optional[Union[Code, CodedConcept]]
            Segmented property type filter to apply.
        algorithm_type: Optional[Union[SegmentAlgorithmTypeValues, str]]
            Segmented property type filter to apply.
        tracking_uid: Optional[str]
            Tracking unique identifier filter to apply.
        tracking_id: Optional[str]
            Tracking identifier filter to apply.

        Returns
        -------
        List[int]
            List of all segment numbers matching the provided criteria.

        """
        filter_funcs = []
        if segment_label is not None:
            filter_funcs.append(
                lambda desc: desc.segment_label == segment_label
            )
        if segmented_property_category is not None:
            filter_funcs.append(
                lambda desc:
                desc.segmented_property_category == segmented_property_category
            )
        if segmented_property_type is not None:
            filter_funcs.append(
                lambda desc:
                desc.segmented_property_type == segmented_property_type
            )
        if algorithm_type is not None:
            algo_type = SegmentAlgorithmTypeValues(algorithm_type)
            filter_funcs.append(
                lambda desc:
                SegmentAlgorithmTypeValues(desc.algorithm_type) == algo_type
            )
        if tracking_uid is not None:
            filter_funcs.append(
                lambda desc: desc.tracking_uid == tracking_uid
            )
        if tracking_id is not None:
            filter_funcs.append(
                lambda desc: desc.tracking_id == tracking_id
            )

        matches = [
            desc.segment_number
            for desc in self.SegmentSequence
            if all(f(desc) for f in filter_funcs)
        ]

        return matches

    def all_tracking_ids(self) -> Set[str]:
        """Get all unique tracking identifiers in this SEG image.

        Returns
        -------
        Set[str]
            All unique tracking identifiers referenced in segment descriptions
            in this SEG image.

        """
        return {
            desc.tracking_id for desc in self.SegmentSequence
            if desc.tracking_id is not None
        }

    def all_tracking_uids(self) -> Set[str]:
        """Get all unique tracking unique identifiers in this SEG image.

        Returns
        -------
        Set[str]
            All unique tracking unique identifiers referenced in segment
            descriptions in this SEG image.

        """
        return {
            desc.tracking_uid for desc in self.SegmentSequence
            if desc.tracking_uid is not None
        }

    def all_segmented_property_categories(self) -> List[CodedConcept]:
        """Get all unique segmented property categories in this SEG image.

        Returns
        -------
        List[CodedConcept]
            All unique segmented property categories referenced in segment
            descriptions in this SEG image.

        """
        categories = []
        for desc in self.SegmentSequence:
            if desc.segmented_property_category not in categories:
                categories.append(desc.segmented_property_category)

        return categories

    def all_segmented_property_types(self) -> List[CodedConcept]:
        """Get all unique segmented property types in this SEG image.

        Returns
        -------
        List[CodedConcept]
            All unique segmented property types referenced in segment
            descriptions in this SEG image.

        """
        types = []
        for desc in self.SegmentSequence:
            if desc.segmented_property_type not in types:
                types.append(desc.segmented_property_type)

        return types

    def _get_src_uid_index(self, sop_instance_uid: str) -> int:
        ind = np.argwhere(self._source_sop_instance_uids == sop_instance_uid)
        if len(ind) == 0:
            raise KeyError(
                f'No such source frame: {sop_instance_uid}'
            )
        return ind.item()

    def list_seg_frames_for_source_frame(
        self,
        source_sop_instance_uid: str
    ) -> List[int]:
        src_ind = self._get_src_uid_index(source_sop_instance_uid)
        seg_frames = np.where(
            self.frame_lut[:, self.lut_src_col] == src_ind
        )[0]
        return seg_frames.tolist()

    def list_segments_in_source_frame(
        self,
        source_sop_instance_uid: str
    ) -> List[int]:
        src_ind = self._get_src_uid_index(source_sop_instance_uid)
        segments = self.frame_lut[
            self.frame_lut[:, self.lut_src_col] == src_ind,
            self.lut_seg_col
        ]
        return segments.tolist()

    def list_seg_frames_for_segment_number(
        self,
        segment_number: int
    ) -> List[int]:
        if segment_number not in self.segment_numbers:
            raise KeyError(
                'No segment found with specified segment number'
            )
        seg_frames = np.where(
            self.frame_lut[:, self.lut_seg_col] == segment_number
        )[0].tolist()
        return seg_frames

    def list_source_frames_for_segment_number(
        self,
        segment_number: int
    ) -> List[str]:
        if segment_number not in self.segment_numbers:
            raise KeyError(
                'No segment found with specified segment number'
            )
        source_frame_indices = self.frame_lut[
            self.frame_lut[:, self.lut_seg_col] == segment_number,
            self.lut_src_col
        ]
        source_frame_uids = self._source_sop_instance_uids[source_frame_indices]
        return source_frame_uids.tolist()

    def _get_pixels_by_seg_frame(
        self,
        seg_frames_matrix: np.ndarray,
        segment_numbers: np.array,
        combine_segments: bool = False,
        relabel: bool = False,
    ) -> np.ndarray:
        """

        seg_frames_matrix: np.ndarray
            Two dimensional numpy array containing integers. Each row of the
            array corresponds to a frame (0th dimension) of the output array.
            Each column of the array corresponds to a segment of the output
            array. Elements specify the frame numbers of the segmentation
            image that contain the pixel data for that output frame and
            segment. A value of -1 signifies that there is no pixel data
            for that frame/segment combination.
        segment_numbers: np.ndarray
            One dimensional numpy array containing segment numbers
            corresponding to the columns of the seg frames matrix.

        """
        if combine_segments:
            if self.segmentation_type != SegmentationTypeValues.BINARY:
                raise ValueError(
                    'Cannot combine segments if the segmentation is not binary'
                )

        # Checks on input array
        if seg_frames_matrix.ndim != 2:
            raise ValueError('Seg frames matrix must be a 2D array.')
        if not issubclass(seg_frames_matrix.dtype.type, np.integer):
            raise TypeError(
                'Seg frames matrix must have an integer data type.'
            )

        if seg_frames_matrix.min() < -1:
            raise ValueError(
                'Seg frames matrix may not contain negative values other than '
                '-1.'
            )
        if seg_frames_matrix.max() > self.NumberOfFrames:
            raise ValueError(
                'Seg frames matrix contains values outside the range of '
                'segmentation frames in the image.'
            )

        if segment_numbers.shape != (seg_frames_matrix.shape[1], ):
            raise ValueError(
                'Segment numbers array does not match the shape of the '
                'seg frames matrix.'
            )

        if (
            segment_numbers.min() < 1 or
            segment_numbers.max() > self.number_of_segments
        ):
            raise ValueError(
                'Segment numbers array contains invalid values.'
                '-1.'
            )

        # Initialize empty pixel array
        pixel_array = np.zeros(
            (
                seg_frames_matrix.shape[0],
                self.Rows,
                self.Columns,
                seg_frames_matrix.shape[1]
            ),
            self.pixel_array.dtype
        )

        # Loop through output frames
        for out_frm, frm_row in enumerate(seg_frames_matrix):
            # Loop through segmentation frames
            for out_seg, seg_frame_num in enumerate(frm_row):
                if seg_frame_num >= 1:
                    seg_frame_ind = seg_frame_num.item() - 1
                    # Copy data to to output array
                    if self.pixel_array.ndim == 2:
                        # Special case with a single segmentation frame
                        pixel_array[out_frm, :, :, out_seg] = \
                            self.pixel_array
                    else:
                        pixel_array[out_frm, :, :, out_seg] = \
                            self.pixel_array[seg_frame_ind, :, :]

        if combine_segments:
            # Check for overlap by summing over the segments dimension
            if np.any(pixel_array.sum(axis=-1) > 1):
                raise RuntimeError(
                    'Segments cannot be combined because they overlap'
                )

            # Scale the array by the segment number using broadcasting
            if relabel:
                pixel_value_map = np.arange(1, len(segment_numbers) + 1)
            else:
                pixel_value_map = segment_numbers
            scaled_array = pixel_array * pixel_value_map.reshape(1, 1, 1, -1)

            # Combine segments by taking maximum down final dimension
            max_array = scaled_array.max(axis=-1)
            pixel_array = max_array

        return pixel_array

    def get_source_sop_instances(self):
        return [self._ref_ins_lut[sop_uid] for sop_uid in self._ref_ins_lut]

    def get_pixels_by_source_frame(
        self,
        source_sop_instance_uids: Sequence[str],
        source_frame_numbers: Optional[Sequence[int]] = None,
        segment_numbers: Optional[Iterable[int]] = None,
        combine_segments: bool = False,
        relabel: bool = False,
        assert_locations_preserved: bool = False,
        assert_missing_frames_are_empty: bool = True
    ):
        # Checks that it is possible to index using source frames in this
        # dataset
        if self._locations_preserved is None:
            if not assert_locations_preserved:
                raise RuntimeError(
                    'Indexing via source frames is not permissible since this '
                    'image does not specify that spatial locations are '
                    'preserved in the course of deriving the segmentation from'
                    'the source image. If you are confident that spatial '
                    'locations are preserved, you may override this behavior '
                    "with the 'assert_locations_preserved' parameter."
                )
        elif self._locations_preserved == SpatialLocationsPreservedValues.NO:
            raise RuntimeError(
                'Indexing via source frames is not permissible since this '
                'image specifies that spatial locations are not preserved '
                'in the course of deriving the segmentation from the source '
                'image.'
            )
        if not self._single_source_frame_per_seg_frame:
            raise RuntimeError(
                'Indexing via source frames is not permissible since some '
                'frames in the segmentation specify multiple source frames.'
            )

        # Checks on validity of the inputs
        if segment_numbers is None:
            segment_numbers = list(self.segment_numbers)
        if len(segment_numbers) == 0:
            raise ValueError(
                'Segment numbers may not be empty.'
            )
        if len(source_sop_instance_uids) == 0:
            raise ValueError(
                'Source SOP instance UIDs may not be empty.'
            )

        if source_frame_numbers is None:
            # Initialize seg frame numbers matrix with value signifying
            # "empty" (-1)
            rows = len(source_sop_instance_uids)
            cols = len(segment_numbers)
            seg_frames = -np.ones(shape=(rows, cols), dtype=np.int32)

            # Sub-matrix of the LUT used for indexing by instance and
            # segment number
            query_cols = [self._lut_src_instance_col, self._lut_seg_col]
            lut = self._frame_lut[:, query_cols]

            # Check for uniqueness
            if np.unique(lut, axis=0).shape[0] != lut.shape[0]:
                raise RuntimeError(
                    'Source SOP instance UIDs and segment numbers do not '
                    'uniquely identify frames of the segmentation image.'
                )

            # Build the segmentation frame matrix
            for r, sop_uid in enumerate(source_sop_instance_uids):
                # Check whether this source UID exists in the LUT
                try:
                    src_uid_ind = self._get_src_uid_index(sop_uid)
                except KeyError as e:
                    if assert_missing_frames_are_empty:
                        continue
                    else:
                        msg = (
                            f'SOP Instance UID {sop_uid} does not match any '
                            'referenced source frames. To return an empty '
                            'segmentation mask in this situation, use the '
                            "'assert_missing_frames_are_empty' parameter."
                        )
                        raise KeyError(msg) from e

                # Iterate over segment numbers for this source instance
                for c, seg_num in enumerate(segment_numbers):
                    # Use LUT to find the segmentation frame containing
                    # the source frame and segment number
                    qry = np.array([src_uid_ind, seg_num])
                    seg_frm_indices = np.where(np.all(lut == qry, axis=1))[0]
                    if len(seg_frm_indices) == 1:
                        seg_frames[r, c] = seg_frm_indices[0] + 1
                    elif len(seg_frm_indices) > 0:
                        # This should never happen due to earlier checks
                        # on unique rows
                        raise RuntimeError(
                            'An unexpected error was encountered during '
                            'indexing of the segmentation image. Please '
                            'file a bug report with the highdicom repository.'
                        )
                    # else no segmentation frame found, assume this frame
                    # is empty and leave the entry in seg_frames as -1

        else:
            if len(source_sop_instance_uids) != 1:
                raise ValueError(
                    'Only a single source SOP instance UID may be specified '
                    'when frame numbers are specified.'
                )
            rows = len(source_frame_numbers)
            cols = len(segment_numbers)
            seg_frames = -np.ones(shape=(rows, cols), dtype=np.int32)

            # Create the sub-matrix of the look up table that indexes
            # by frame number and segment number within this
            # instance
            src_uid_ind = self._get_src_uid_index(source_sop_instance_uids[0])
            col = self._lut_src_instance_col
            lut_instance_mask = self._frame_lut[:, col] == src_uid_ind
            lut = self._frame_lut[lut_instance_mask, :]
            query_cols = [self._lut_src_frame_col, self._lut_seg_col]
            lut = lut[:, query_cols]

            if np.unique(lut, axis=0).shape[0] != lut.shape[0]:
                raise RuntimeError(
                    'Source frame numbers and segment numbers do not '
                    'uniquely identify frames of the segmentation image.'
                )

            # Build the segmentation frame matrix
            for r, src_frm_num in enumerate(source_frame_numbers):
                # Check whether this source frame exists in the LUT
                if src_frm_num not in lut[:, 1]:
                    if assert_missing_frames_are_empty:
                        continue
                    else:
                        msg = (
                            f'Source frame number {src_frm_num} does not '
                            'match any referenced source frame. To return '
                            'an empty segmentation mask in this situation, '
                            "use the 'assert_missing_frames_are_empty' "
                            'parameter.'
                        )
                        raise KeyError(msg)

                # Iterate over segment numbers for this source frame
                for c, seg_num in enumerate(segment_numbers):
                    # Use LUT to find the segmentation frame containing
                    # the source frame and segment number
                    qry = np.array([src_frm_num, seg_num])
                    seg_frm_indices = np.where(np.all(lut == qry, axis=1))[0]
                    if len(seg_frm_indices) == 1:
                        seg_frames[r, c] = seg_frm_indices[0] + 1
                    elif len(seg_frm_indices) > 0:
                        # This should never happen due to earlier checks
                        # on unique rows
                        raise RuntimeError(
                            'An unexpected error was encountered during '
                            'indexing of the segmentation image. Please '
                            'file a bug report with the highdicom repository.'
                        )
                    # else no segmentation frame found, assume this frame
                    # is empty and leave the entry in seg_frames as -1

        return self._get_pixels_by_seg_frame(
            seg_frames_matrix=seg_frames,
            segment_numbers=np.array(segment_numbers),
            combine_segments=combine_segments,
            relabel=relabel,
        )

    def get_pixels(
        self,
        source_frames: Optional[Iterable[str]] = None,
        segment_numbers: Optional[Iterable[int]] = None,
        combine_segments: bool = False,
        relabel: bool = False,
    ) -> np.ndarray:
        """Get a numpy array of the reconstructed segmentation.

        Parameters
        ----------
        source_frames: Optional[Iterable[str]]
            Iterable containing SOP Instance UIDs of the source images to
            include in the segmentation. If unspecified, all source images
            are included.
        segment_numbers: Optional[Sequence[int]]
            Sequence containing segment numbers to include. If unspecified,
            all segments are included.
        combine_segments: bool
            If True, combine the different segments into a single label
            map in which the value of a pixel represents its segment.
            If False (the default), segments are binary and stacked down the
            last dimension of the output array.
        relabel: bool
            If True and ``combine_segments`` is ``True``, the pixel values in
            the output array are relabelled into the range ``0`` to
            ``len(segment_numbers)`` (inclusive) accoring to the position of
            the original segment numbers in ``segment_numbers`` parameter.  If
            ``combine_segments`` is ``False``, this has no effect.

        Returns
        -------
        pixel_array: np.ndarray
            Pixel array representing the segmentation

        Note
        ----
        The output array will have 4 dimensions under the default behavior,
        and 3 dimensions if ``combine_segments`` is set to ``True``.
        The first dimension represents the source frames. If ``source_frames``
        has been specified, then ``pixel_array[i, ...]`` represents the
        segmentation of ``source_frames[i]``. If ``source_frames`` was not
        specified, then ``pixel_array[i, ...]`` represents the segmentation of
        ``parser._source_sop_instance_uids[i]``.
        The next two dimensions are the rows and columns of the frames,
        respectively.
        When ``combine_segments`` is ``False`` (the default behavior), the
        segments are stacked down the final (4th) dimension of the pixel array.
        If ``segment_numbers`` was specified, then ``pixel_array[:, :, :, i]``
        represents the data for segment ``segment_numbers[i]``. If
        ``segment_numbers`` was unspecified, then ``pixel_array[:, :, :, i]``
        represents the data for segment ``parser.segment_numbers[i]``. Note
        that in neither case does ``pixel_array[:, :, :, i]`` represent
        the segmentation data for the segment with segment number ``i``, since
        segment numbers begin at 1 in DICOM.
        When ``combine_segments`` is ``True``, then the segmentation data from
        all specified segments is combined into a multi-class array in which
        pixel value is used to denote the segment to which a pixel belongs.
        This is only possible if the type of the segmentation is ``BINARY`` and
        the segments do not overlap. If the segments do overlap, a
        ``RuntimeError`` will be raised. After combining, the value of a pixel
        depends upon the ``relabel`` parameter. In both
        cases, pixels that appear in no segments with have a value of ``0``.
        If ``relabel`` is ``False``, a pixel that appears
        in the segment with segment number ``i`` (according to the original
        segment numbering of the segmentation object) will have a value of
        ``i``. If ``relabel`` is ``True``, the value of
        a pixel in segment ``i`` is related not to the original segment number,
        but to the index of that segment number in the ``segment_numbers``
        parameter of this method. Specifically, pixels belonging to the
        segment with segment number ``segment_numbers[i]`` is given the value
        ``i + 1`` in the output pixel array (since 0 is reserved for pixels
        that belong to no segments). In this case, the values in the output
        pixel array will always lie in the range ``0`` to
        ``len(segment_numbers)`` inclusive.
        """
        if combine_segments:
            if self.segmentation_type != SegmentationTypeValues.BINARY:
                raise ValueError(
                    'Cannot combine segments if the segmentation is not binary'
                )

        # If no source frames were specified, use all source frames
        if source_frames is None:
            source_frames = self._source_sop_instance_uids

        # If no segments were specified, use all segments
        if segment_numbers is None:
            segment_numbers = self.segment_numbers

        # Check all provided segmentation numbers are valid
        seg_num_arr = np.array(segment_numbers)
        seg_nums_exist = np.in1d(
            seg_num_arr, np.array(self.segment_numbers)
        )
        if not seg_nums_exist.all():
            missing_seg_nums = seg_num_arr[np.logical_not(seg_nums_exist)]
            raise KeyError(
                'Segment numbers contained non-existent segments: '
                f'{missing_seg_nums}'
            )

        # Check segment numbers are unique
        if len(np.unique(seg_num_arr)) != len(seg_num_arr):
            raise ValueError(
                'Segment numbers must not contain duplicates'
            )

        # Initialize empty pixel array
        pixel_array = np.zeros(
            (
                len(source_frames),
                self.Rows,
                self.Columns,
                len(segment_numbers)
            ),
            self.pixel_array.dtype
        )

        # Loop through source frames
        for out_ind, src_frame in enumerate(source_frames):
            src_ind = self._get_src_fm_index(src_frame)

            # Find segmentation frames relating to this source frame
            seg_frames = np.where(
                self.frame_lut[:, self.lut_src_col] == src_ind
            )[0]
            seg_nums_for_frame = self.frame_lut[seg_frames, self.lut_seg_col]

            # Loop through segmentation frames
            for seg_frame, seg_num in zip(seg_frames, seg_nums_for_frame):
                # Find output index for this segmentation number (if any)
                seg_ind = np.where(seg_num_arr == seg_num)[0]

                if len(seg_ind) > 0:
                    # Copy data to to output array
                    if self.pixel_array.ndim == 2:
                        # Special case with a single segmentation frame
                        pixel_array[out_ind, :, :, seg_ind.item()] = \
                            self.pixel_array
                    else:
                        pixel_array[out_ind, :, :, seg_ind.item()] = \
                            self.pixel_array[seg_frame, :, :]

        if combine_segments:
            # Check for overlap by summing over the segments dimension
            if np.any(pixel_array.sum(axis=-1) > 1):
                raise RuntimeError(
                    'Segments cannot be combined because they overlap'
                )

            # Scale the array by the segment number using broadcasting
            if relabel:
                pixel_value_map = np.arange(1, len(segment_numbers) + 1)
            else:
                pixel_value_map = seg_num_arr
            scaled_array = pixel_array * pixel_value_map.reshape(1, 1, 1, -1)

            # Combine segments by taking maximum down final dimension
            max_array = scaled_array.max(axis=-1)
            pixel_array = max_array

        return pixel_array

    # TODO
    # Multi-frame source image
        # Segs with a single frame
        # Allow binary fractional segs to combine segments
    # Option to force standards compliance
    # Integrity checks:
    #    Each source frame specified in pffgs exists
    #    No combination of source frame and segment number has
    #    multiple source frames
    #    Tracking IDs do not have multiple segments with different
    #    segmented properties
    # Lazy decoding of segmentation frames
    # Should functions raise error or return empty lists?
    # Standardize method names
    # Correct for spatial ordering of source frames?
    # Optimize combine_segments to exploit sparse nature of array
    # Implement from_dataset from DimensionIndexSequence
    # Allowing indexing from multiple source series
    # Index by spatial location
    # Get fill pixel array with metadata
