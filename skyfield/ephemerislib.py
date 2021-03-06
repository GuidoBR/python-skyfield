"""Compute position by chaining together answers from different ephemerides."""

from collections import namedtuple
from numpy import max, min

from .constants import C_AUDAY
from .functions import length_of
from .positionlib import Astrometric, Barycentric, ICRS
from .timelib import JulianDate, takes_julian_date
from .units import Distance, Velocity

Segment = namedtuple('Segment', 'center target compute')


class Body(object):
    def __init__(self, ephemeris, code):
        self.ephemeris = ephemeris
        self.segments = ephemeris.segments
        self.code = code

    @takes_julian_date
    def at(self, jd):
        """Compute the Solar System position of this body at a given time."""
        segments = self.segments
        segment_dict = dict((segment.target, segment) for segment in segments)
        chain = list(_center(self.code, segment_dict))[::-1]
        pos, vel = _tally((), chain, jd)
        return Barycentric(pos, vel, jd)

    def geometry_of(self, body):
        if not isinstance(body, Body):
            code = self.ephemeris.decode(body)
            body = Body(self.ephemeris, code)
        center_chain, target_chain = _connect(self, body)
        return Geometry(self.code, body.code, center_chain, target_chain)

    def observe(self, body):
        segments = self.segments
        center = self.code
        if isinstance(body, Body):
            segments += body.segments
            target = body.code
        else:
            target = self.ephemeris.decode(body)
        segment_dict = dict((segment.target, segment) for segment in segments)
        center_chain = list(_center(center, segment_dict))[::-1]
        target_chain = list(_center(target, segment_dict))[::-1]
        if not center_chain[0].center == target_chain[0].center == 0:
            raise ValueError('cannot observe() unless both bodies can be'
                             ' referenced to the solar system barycenter')
        return Solution(center, target, center_chain, target_chain)

    def topos(self, latitude=None, longitude=None, latitude_degrees=None,
              longitude_degrees=None, elevation_m=0.0, x=0.0, y=0.0):
        assert self.code == 399
        from .toposlib import Topos
        t = Topos(latitude, longitude, latitude_degrees,
                  longitude_degrees, elevation_m, x, y)
        t.ephemeris = self.ephemeris
        t.segments += self.segments
        return t


def _connect(body1, body2):
    """Return ``(sign, segment)`` tuple list leading from body1 to body2."""
    every = body1.segments + body2.segments
    segment_dict = dict((segment.target, segment) for segment in every)
    segments1 = list(_center(body1.code, segment_dict))[::-1]
    segments2 = list(_center(body2.code, segment_dict))[::-1]
    if segments1[0].center != segments2[0].center:
        raise ValueError('cannot trace these bodies back to a common center')
    i = sum(1 for s1, s2 in zip(segments1, segments2) if s1.target == s2.target)
    return segments1[i:], segments2[i:]


def _center(code, segment_dict):
    """Starting with `code`, follow segments from target to center."""
    while code in segment_dict:
        segment = segment_dict[code]
        yield segment
        code = segment.center


class Geometry(object):
    def __init__(self, center, target, center_chain, target_chain):
        self.center = center
        self.target = target
        self.center_chain = center_chain
        self.target_chain = target_chain

    def __str__(self):
        return 'Geometry\n{0}'.format('\n'.join(
            ' {0}'.format(c)
            for c in self.center_chain + self.target_chain))

    @takes_julian_date
    def at(self, jd):
        """Return the geometric Cartesian position and velocity."""
        pos, vel = _tally(self.center_chain, self.target_chain, jd)
        cls = Barycentric if self.center == 0 else ICRS
        return cls(pos, vel, jd)


class Solution(object):
    def __init__(self, center, target, center_chain, target_chain):
        self.center = center
        self.target = target
        self.center_chain = center_chain
        self.target_chain = target_chain

    def __str__(self):
        lines = [' - {0}'.format(c) for c in self.center_chain]
        lines.extend(' + {0}'.format(c) for c in self.target_chain)
        return 'Solution center={0} target={1}:\n{2}'.format(
            self.center, self.target, '\n'.join(lines))

    @takes_julian_date
    def at(self, jd):
        """Return a light-time corrected astrometric position and velocity."""
        cposition, cvelocity = _tally([], self.center_chain, jd)
        tposition, tvelocity = _tally([], self.target_chain, jd)
        distance = length_of(tposition - cposition)
        lighttime0 = 0.0
        jd_tdb = jd.tdb
        for i in range(10):
            lighttime = distance / C_AUDAY
            delta = lighttime - lighttime0
            if -1e-12 < min(delta) and max(delta) < 1e-12:
                break
            jd2 = JulianDate(tdb=jd_tdb - lighttime)
            tposition, tvelocity = _tally([], self.target_chain, jd2)
            distance = length_of(tposition - cposition)
            lighttime0 = lighttime
        else:
            raise ValueError('observe_from() light-travel time'
                             ' failed to converge')
        cls = Barycentric if self.center == 0 else Astrometric
        pos = cls(tposition - cposition, tvelocity - cvelocity, jd)
        pos.lighttime = lighttime
        class Observer(object):
            pass
        pos.observer = Observer()
        pos.observer.position = Distance(cposition)
        pos.observer.velocity = Velocity(cvelocity)
        pos.observer.geocentric = False  # TODO
        #pos.observer.ephemeris = None
        if hasattr(self.center, '_altaz_rotation'):
            pos.observer.topos = self.center
            pos.observer.altaz_rotation = self.center._altaz_rotation(jd)
        return pos


def _tally(minus_chain, plus_chain, jd):
    position = velocity = 0.0
    for segment in minus_chain:
        p, v = segment.compute(jd)
        position -= p
        velocity -= v
    for segment in plus_chain:
        p, v = segment.compute(jd)
        position += p
        velocity += v
    return position, velocity
