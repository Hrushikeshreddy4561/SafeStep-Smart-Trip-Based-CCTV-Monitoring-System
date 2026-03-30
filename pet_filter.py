# =============================================================================
# pet_filter.py — Pet / Small-Object Motion Filter
#
# PROBLEM:
#   Cats, dogs, and small animals trigger motion detection but should NOT
#   generate security alerts. They move close to the floor and are small.
#
# SOLUTION:
#   Inspect the bounding rectangle of each motion contour.
#   If width, height, AND area are all below pet thresholds, classify it
#   as a pet/minor movement and discard it.
#
# LIMITATION:
#   This is size-based heuristics — a large dog will not be filtered.
#   For a production system you would add an object classification model.
# =============================================================================

import cv2
import config


class PetFilter:
    """
    Classifies motion contours as either 'pet-sized' or 'person-sized'.
    """

    def filter(self, contours):
        """
        Split contours into two groups.

        Parameters
        ----------
        contours : list
            Contours returned by MotionDetector.detect()

        Returns
        -------
        person_contours : list
            Contours large enough to be a person.
        pet_contours : list
            Contours that are small — likely pets or minor movement.
        """
        person_contours = []
        pet_contours    = []

        for contour in contours:
            # Get the bounding rectangle for this motion blob
            x, y, w, h = cv2.boundingRect(contour)
            area        = cv2.contourArea(contour)

            if self._is_pet(w, h, area):
                pet_contours.append(contour)
            else:
                person_contours.append(contour)

        return person_contours, pet_contours

    def _is_pet(self, width, height, area):
        """
        Return True if the contour dimensions suggest a small animal.

        A contour is considered pet-sized only when ALL three conditions hold:
          - width is small
          - height is small
          - total area is small
        Using AND (not OR) avoids accidentally filtering a person who is
        temporarily partially occluded or far from the camera.
        """
        return (width  <= config.PET_MAX_WIDTH  and
                height <= config.PET_MAX_HEIGHT and
                area   <= config.PET_MAX_AREA)

    def get_largest_contour(self, contours):
        """
        Return the contour with the largest area, or None if list is empty.
        Useful for finding the primary subject in frame.
        """
        if not contours:
            return None
        return max(contours, key=cv2.contourArea)

    def get_bounding_box(self, contour):
        """Return (x, y, w, h) bounding box for a single contour."""
        return cv2.boundingRect(contour)
