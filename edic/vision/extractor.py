"""All per-person signals for one photo."""
from ..types import Person
from .bib import BibProfile, BibReader
from .colour import colour_hist
from .faces import FaceEngine
from .pose import PoseModel


class Extractor:
    def __init__(self, pose: PoseModel, faces: FaceEngine, bib: BibReader):
        self.pose, self.faces, self.bib = pose, faces, bib

    def extract(self, img, profile: BibProfile | None = None) -> list[Person]:
        people = self.pose.detect(img)
        for p in people:
            p.numbers, p.bibs = self.bib.read(img, p, profile)
            p.colour = colour_hist(img, p)
            self.faces.embed_person(img, p)
        return people
