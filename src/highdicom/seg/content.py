"""Data Elements that are specific to the Segmentation IOD."""
from typing import List, Optional, Sequence, Tuple, Union

import numpy as np
from pydicom.datadict import tag_for_keyword
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence as DataElementSequence
from pydicom.sr.coding import Code

from highdicom.content import (
    AlgorithmIdentificationSequence,
    PlanePositionSequence,
)
from highdicom.enum import CoordinateSystemNames
from highdicom.seg.enum import SegmentAlgorithmTypeValues
from highdicom.sr.coding import CodedConcept
from highdicom.utils import compute_plane_position_slide_per_frame


class SegmentDescription(Dataset):

    """Dataset describing a segment based on the Segment Description macro."""

    def __init__(
            self,
            segment_number: int,
            segment_label: str,
            segmented_property_category: Union[Code, CodedConcept],
            segmented_property_type: Union[Code, CodedConcept],
            algorithm_type: Union[SegmentAlgorithmTypeValues, str],
            algorithm_identification: Optional[
                AlgorithmIdentificationSequence
            ] = None,
            tracking_uid: Optional[str] = None,
            tracking_id: Optional[str] = None,
            anatomic_regions: Optional[
                Sequence[Union[Code, CodedConcept]]
            ] = None,
            primary_anatomic_structures: Optional[
                Sequence[Union[Code, CodedConcept]]
            ] = None
        ) -> None:
        """
        Parameters
        ----------
        segment_number: int
            Number of the segment
        segment_label: str
            Label of the segment
        segmented_property_category: Union[pydicom.sr.coding.Code, highdicom.sr.coding.CodedConcept]
            Category of the property the segment represents,
            e.g. ``Code("49755003", "SCT", "Morphologically Abnormal Structure")``
            (see `CID 7150 <http://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_CID_7150.html>`_
            "Segmentation Property Categories")
        segmented_property_type: Union[pydicom.sr.coding.Code, highdicom.sr.coding.CodedConcept]
            Property the segment represents,
            e.g. ``Code("108369006", "SCT", "Neoplasm")``
            (see `CID 7151 <http://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_CID_7151.html>`_
            "Segmentation Property Types")
        algorithm_type: Union[str, highdicom.seg.enum.SegmentAlgorithmTypeValues]
            Type of algorithm
        algorithm_identification: highdicom.content.AlgorithmIdentificationSequence, optional
            Information useful for identification of the algorithm, such
            as its name or version. Required unless the algorithm type is `MANUAL`
        tracking_uid: str, optional
            Unique tracking identifier (universally unique)
        tracking_id: str, optional
            Tracking identifier (unique only with the domain of use)
        anatomic_regions: Sequence[Union[pydicom.sr.coding.Code, highdicom.sr.coding.CodedConcept]], optional
            Anatomic region(s) into which segment falls,
            e.g. ``Code("41216001", "SCT", "Prostate")``
            (see `CID 4 <http://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_CID_4.html>`_
            "Anatomic Region", `CID 4031 <http://dicom.nema.org/medical/dicom/current/output/chtml/part16/sect_CID_4031.html>`_ "Common Anatomic Regions", as
            as well as other CIDs for domain-specific anatomic regions)
        primary_anatomic_structures: Sequence[Union[highdicom.sr.coding.Code, highdicom.sr.coding.CodedConcept]], optional
            Anatomic structure(s) the segment represents
            (see CIDs for domain-specific primary anatomic structures)

        """  # noqa
        super().__init__()
        self.SegmentNumber = segment_number
        self.SegmentLabel = segment_label
        self.SegmentedPropertyCategoryCodeSequence = [
            CodedConcept(
                segmented_property_category.value,
                segmented_property_category.scheme_designator,
                segmented_property_category.meaning,
                segmented_property_category.scheme_version
            ),
        ]
        self.SegmentedPropertyTypeCodeSequence = [
            CodedConcept(
                segmented_property_type.value,
                segmented_property_type.scheme_designator,
                segmented_property_type.meaning,
                segmented_property_type.scheme_version
            ),
        ]
        algorithm_type = SegmentAlgorithmTypeValues(algorithm_type)
        self.SegmentAlgorithmType = algorithm_type.value
        if algorithm_identification is None:
            if (
                self.SegmentAlgorithmType !=
                SegmentAlgorithmTypeValues.MANUAL.value
            ):
                raise TypeError(
                    "Algorithm identification sequence is required "
                    "unless the segmentation type is MANUAL"
                )
        else:
            self.SegmentAlgorithmName = \
                algorithm_identification[0].AlgorithmName
            self.SegmentationAlgorithmIdentificationSequence = \
                algorithm_identification
        num_given_tracking_identifiers = sum([
            tracking_id is not None,
            tracking_uid is not None
        ])
        if num_given_tracking_identifiers == 2:
            self.TrackingID = tracking_id
            self.TrackingUID = tracking_uid
        elif num_given_tracking_identifiers == 1:
            raise TypeError(
                'Tracking ID and Tracking UID must both be provided.'
            )
        if anatomic_regions is not None:
            self.AnatomicRegionSequence = [
                CodedConcept(
                    region.value,
                    region.scheme_designator,
                    region.meaning,
                    region.scheme_version
                )
                for region in anatomic_regions
            ]
        if primary_anatomic_structures is not None:
            self.PrimaryAnatomicStructureSequence = [
                CodedConcept(
                    structure.value,
                    structure.scheme_designator,
                    structure.meaning,
                    structure.scheme_version
                )
                for structure in primary_anatomic_structures
            ]


class DimensionIndexSequence(DataElementSequence):

    """Sequence of data elements describing dimension indices for the patient
    or slide coordinate system based on the Dimension Index functional
    group macro.

    Note
    ----
    The order of indices is fixed.

    """

    def __init__(
        self,
        coordinate_system: Union[str, CoordinateSystemNames]
    ) -> None:
        """
        Parameters
        ----------
        coordinate_system: Union[str, highdicom.enum.CoordinateSystemNames]
            Subject (``"PATIENT"`` or ``"SLIDE"``) that was the target of
            imaging

        """
        super().__init__()
        self._coordinate_system = CoordinateSystemNames(coordinate_system)
        if self._coordinate_system == CoordinateSystemNames.SLIDE:
            dim_uid = '1.2.826.0.1.3680043.9.7433.2.4'

            segment_number_index = Dataset()
            segment_number_index.DimensionIndexPointer = tag_for_keyword(
                'ReferencedSegmentNumber'
            )
            segment_number_index.FunctionalGroupPointer = tag_for_keyword(
                'SegmentIdentificationSequence'
            )
            segment_number_index.DimensionOrganizationUID = dim_uid
            segment_number_index.DimensionDescriptionLabel = 'Segment Number'

            x_axis_index = Dataset()
            x_axis_index.DimensionIndexPointer = tag_for_keyword(
                'XOffsetInSlideCoordinateSystem'
            )
            x_axis_index.FunctionalGroupPointer = tag_for_keyword(
                'PlanePositionSlideSequence'
            )
            x_axis_index.DimensionOrganizationUID = dim_uid
            x_axis_index.DimensionDescriptionLabel = \
                'X Offset in Slide Coordinate System'

            y_axis_index = Dataset()
            y_axis_index.DimensionIndexPointer = tag_for_keyword(
                'YOffsetInSlideCoordinateSystem'
            )
            y_axis_index.FunctionalGroupPointer = tag_for_keyword(
                'PlanePositionSlideSequence'
            )
            y_axis_index.DimensionOrganizationUID = dim_uid
            y_axis_index.DimensionDescriptionLabel = \
                'Y Offset in Slide Coordinate System'

            z_axis_index = Dataset()
            z_axis_index.DimensionIndexPointer = tag_for_keyword(
                'ZOffsetInSlideCoordinateSystem'
            )
            z_axis_index.FunctionalGroupPointer = tag_for_keyword(
                'PlanePositionSlideSequence'
            )
            z_axis_index.DimensionOrganizationUID = dim_uid
            z_axis_index.DimensionDescriptionLabel = \
                'Z Offset in Slide Coordinate System'

            row_dimension_index = Dataset()
            row_dimension_index.DimensionIndexPointer = tag_for_keyword(
                'ColumnPositionInTotalImagePixelMatrix'
            )
            row_dimension_index.FunctionalGroupPointer = tag_for_keyword(
                'PlanePositionSlideSequence'
            )
            row_dimension_index.DimensionOrganizationUID = dim_uid
            row_dimension_index.DimensionDescriptionLabel = \
                'Column Position In Total Image Pixel Matrix'

            column_dimension_index = Dataset()
            column_dimension_index.DimensionIndexPointer = tag_for_keyword(
                'RowPositionInTotalImagePixelMatrix'
            )
            column_dimension_index.FunctionalGroupPointer = tag_for_keyword(
                'PlanePositionSlideSequence'
            )
            column_dimension_index.DimensionOrganizationUID = dim_uid
            column_dimension_index.DimensionDescriptionLabel = \
                'Row Position In Total Image Pixel Matrix'

            # Organize frames for each segment similar to TILED_FULL, first
            # along the row dimension (column indices from left to right) and
            # then along the column dimension (row indices from top to bottom)
            # of the Total Pixel Matrix.
            self.extend([
                segment_number_index,
                row_dimension_index,
                column_dimension_index,
                x_axis_index,
                y_axis_index,
                z_axis_index,
            ])

        elif self._coordinate_system == CoordinateSystemNames.PATIENT:
            dim_uid = '1.2.826.0.1.3680043.9.7433.2.3'

            segment_number_index = Dataset()
            segment_number_index.DimensionIndexPointer = tag_for_keyword(
                'ReferencedSegmentNumber'
            )
            segment_number_index.FunctionalGroupPointer = tag_for_keyword(
                'SegmentIdentificationSequence'
            )
            segment_number_index.DimensionOrganizationUID = dim_uid
            segment_number_index.DimensionDescriptionLabel = 'Segment Number'

            image_position_index = Dataset()
            image_position_index.DimensionIndexPointer = tag_for_keyword(
                'ImagePositionPatient'
            )
            image_position_index.FunctionalGroupPointer = tag_for_keyword(
                'PlanePositionSequence'
            )
            image_position_index.DimensionOrganizationUID = dim_uid
            image_position_index.DimensionDescriptionLabel = \
                'Image Position Patient'

            self.extend([
                segment_number_index,
                image_position_index,
            ])

        else:
            raise ValueError(
                f'Unknown coordinate system "{self._coordinat_system}"'
            )

    def get_plane_positions_of_image(
        self,
        image: Dataset
    ) -> List[PlanePositionSequence]:
        """Gets plane positions of frames in multi-frame image.

        Parameters
        ----------
        image: Dataset
            Multi-frame image

        Returns
        -------
        List[PlanePositionSequence]
            Plane position of each frame in the image

        """
        is_multiframe = hasattr(image, 'NumberOfFrames')
        if not is_multiframe:
            raise ValueError('Argument "image" must be a multi-frame image.')

        if self._coordinate_system == CoordinateSystemNames.SLIDE:
            if hasattr(image, 'PerFrameFunctionalGroupsSequence'):
                plane_positions = [
                    item.PlanePositionSlideSequence
                    for item in image.PerFrameFunctionalGroupsSequence
                ]
            else:
                # If Dimension Organization Type is TILED_FULL, plane
                # positions are implicit and need to be computed.
                plane_positions = compute_plane_position_slide_per_frame(
                    image
                )
        else:
            plane_positions = [
                item.PlanePositionSequence
                for item in image.PerFrameFunctionalGroupsSequence
            ]

        return plane_positions

    def get_plane_positions_of_series(
        self,
        images: Sequence[Dataset]
    ) -> List[PlanePositionSequence]:
        """Gets plane positions for series of single-frame images.

        Parameters
        ----------
        images: Sequence[Dataset]
            Series of single-frame images

        Returns
        -------
        List[PlanePositionSequence]
            Plane position of each frame in the image

        """
        is_multiframe = any([hasattr(img, 'NumberOfFrames') for img in images])
        if is_multiframe:
            raise ValueError(
                'Argument "images" must be a series of single-frame images.'
            )

        plane_positions = [
            PlanePositionSequence(
                coordinate_system=CoordinateSystemNames.PATIENT,
                image_position=img.ImagePositionPatient
            )
            for img in images
        ]

        return plane_positions

    def get_index_values(
        self,
        plane_positions: Sequence[PlanePositionSequence]
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Get the values of indexed attributes.

        Parameters
        ----------
        plane_positions: Sequence[PlanePositionSequence]
            Plane position of frames in a multi-frame image or in a series of
            single-frame images

        Returns
        -------
        Tuple[numpy.ndarray, numpy.ndarray]
            2D array of dimension index values and 1D array of planes indices
            for sorting frames according to their spatial position specified
            by the dimension index.

        """
        # For each dimension other than the Referenced Segment Number,
        # obtain the value of the attribute that the Dimension Index Pointer
        # points to in the element of the Plane Position Sequence or
        # Plane Position Slide Sequence.
        # Per definition, this is the Image Position Patient attribute
        # in case of the patient coordinate system, or the
        # X/Y/Z Offset In Slide Coordinate System and the Column/Row
        # Position in Total Image Pixel Matrix attributes in case of the
        # the slide coordinate system.
        plane_position_values = np.array([
            [
                np.array(p[0][indexer.DimensionIndexPointer].value)
                for indexer in self[1:]
            ]
            for p in plane_positions
        ])

        # Build an array that can be used to sort planes according to the
        # Dimension Index Value based on the order of the items in the
        # Dimension Index Sequence.
        _, plane_sort_indices = np.unique(
            plane_position_values,
            axis=0,
            return_index=True
        )

        return (plane_position_values, plane_sort_indices)
