import enum
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class CPUBrand(str, enum.Enum):
    AMD = "amd"
    INTEL = "intel"


class GPUBrand(str, enum.Enum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"


class DDRGeneration(str, enum.Enum):
    DDR4 = "ddr4"
    DDR5 = "ddr5"
    DDR6 = "ddr6"


class CoolerType(str, enum.Enum):
    AIR = "air"
    AIO_120 = "aio_120"
    AIO_140 = "aio_140"
    AIO_240 = "aio_240"
    AIO_280 = "aio_280"
    AIO_360 = "aio_360"


class FormFactor(str, enum.Enum):
    ATX = "atx"
    MATX = "matx"
    ITX = "itx"
    EATX = "eatx"
    # Server/workstation board sizes. SSI-EEB and SSI-CEB are what most
    # Threadripper PRO, EPYC and dual-socket Xeon boards actually ship as —
    # they are wider than E-ATX and only fit cases that list them explicitly,
    # so collapsing them into "eatx" would produce builds that don't physically
    # assemble.
    SSI_EEB = "ssi_eeb"
    SSI_CEB = "ssi_ceb"


class StorageInterface(str, enum.Enum):
    PCIE_GEN3 = "pcie_gen3"
    PCIE_GEN4 = "pcie_gen4"
    PCIE_GEN5 = "pcie_gen5"
    SATA3 = "sata3"


class PSUEfficiency(str, enum.Enum):
    PLUS_80 = "80plus"
    BRONZE = "80plus_bronze"
    SILVER = "80plus_silver"
    GOLD = "80plus_gold"
    PLATINUM = "80plus_platinum"
    TITANIUM = "80plus_titanium"


class PSUFormFactor(str, enum.Enum):
    ATX = "atx"
    SFX = "sfx"
    SFX_L = "sfx_l"


class ModularType(str, enum.Enum):
    FULL = "full"
    SEMI = "semi"
    NON = "non"


class MemoryModuleType(str, enum.Enum):
    """Physical/electrical DIMM type. Not interchangeable in either direction:
    Threadripper PRO and EPYC require registered modules and will not POST on
    unbuffered ones, while consumer AM5/LGA1851 boards reject registered ones.
    `RAMGroup.is_ecc` alone can't express that, since ECC UDIMMs exist."""

    UDIMM = "udimm"  # unbuffered — every consumer platform, ECC or not
    RDIMM = "rdimm"  # registered — Threadripper PRO, EPYC, Xeon W/SP
    LRDIMM = "lrdimm"  # load-reduced — highest-capacity server configs


class CaseSize(str, enum.Enum):
    FULL_TOWER = "full_tower"
    MID_TOWER = "mid_tower"
    MINI_TOWER = "mini_tower"
    SFF = "sff"


class PCPart(Base):
    __tablename__ = "pc_parts"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    name = Column(String(255), nullable=False)
    manufacturer = Column(String(255), nullable=True)
    model_number = Column(String(255), nullable=True)
    year_released = Column(Integer, nullable=True)

    part_type = Column(String(50), nullable=False)

    msrp_cents = Column(Integer, nullable=True)
    street_price_cents = Column(Integer, nullable=True)
    price_source = Column(String(20), nullable=True)
    last_price_checked_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the pricing ETL last ran a SerpAPI check for this part",
    )
    used_market_viable = Column(Boolean, nullable=True, server_default="false")

    # Product image, admin-uploaded to GCS (see admin/src/lib/storage.ts). The
    # credit fields exist because the images are sourced from manufacturer
    # press/product pages rather than owned outright — every displayed image
    # carries its attribution, and image_source_url records where it came from
    # so the licensing basis can be re-checked later.
    image_url = Column(String(500), nullable=True)
    image_credit = Column(String(255), nullable=True)  # e.g. "Image: Fractal Design"
    image_source_url = Column(String(500), nullable=True)
    image_license = Column(
        String(100), nullable=True, comment="e.g. 'manufacturer press kit', 'CC BY 4.0'"
    )

    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    listings = relationship(
        "Listing",
        back_populates="part",
        cascade="all, delete-orphan",
    )

    __mapper_args__ = {
        "polymorphic_on": part_type,
        "polymorphic_identity": "part",
    }


# --- Component groups ---------------------------------------------------------
# A "group" holds what is shared across every purchasable variant of the same
# part (e.g. one GPUChipset row per "RTX 5080", shared by every MSI / Gigabyte
# / PNY board). Groups are NOT pc_parts rows: nothing FKs them from pc_parts,
# and the link runs the other way, from the PCPart subclass (gpus.
# gpu_chipset_id and siblings).
#
# What lives on the group has grown. It began as intrinsic spec only, then took
# street_price_cents (f6a7b8c9d0e1), and now takes listings too
# (e5a7c9b1d3f5) — one eBay search URL is right for every board of a chipset.
# The direction of the FK is why resolving a part's group costs a lookup in its
# subtype table rather than a column read on pc_parts.


class GPUChipset(Base):
    __tablename__ = "gpu_chipsets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(100), nullable=False)  # e.g. "RTX 5080"

    # Street price lives on the group (shared by every board of this chipset).
    street_price_cents = Column(Integer, nullable=True)
    price_source = Column(String(20), nullable=True)
    last_price_checked_at = Column(DateTime(timezone=True), nullable=True)

    vram_gb = Column(Integer, nullable=False)
    vram_type = Column(String(20), nullable=True)
    tdp_watts = Column(Integer, nullable=False)
    recommended_psu_watts = Column(Integer, nullable=True)
    pcie_generation = Column(Integer, nullable=True)
    base_clock_mhz = Column(Integer, nullable=True)
    boost_clock_mhz = Column(Integer, nullable=True)
    has_ray_tracing = Column(Boolean, nullable=True)
    cuda_cores = Column(Integer, nullable=True)  # Nvidia
    tensor_cores = Column(Integer, nullable=True)  # Nvidia
    stream_processors = Column(Integer, nullable=True)  # AMD
    matrix_cores = Column(Integer, nullable=True)  # AMD
    supported_features = Column(ARRAY(String), nullable=True)
    benchmark_scores = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    variants = relationship("GPU", back_populates="chipset")


class PSUGroup(Base):
    __tablename__ = "psu_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(150), nullable=False)  # e.g. "850W 80+ Gold ATX Fully-Modular"

    # Street price lives on the group (shared by every unit in this spec).
    street_price_cents = Column(Integer, nullable=True)
    price_source = Column(String(20), nullable=True)
    last_price_checked_at = Column(DateTime(timezone=True), nullable=True)

    wattage = Column(Integer, nullable=False)
    form_factor = Column(String(10), nullable=False)
    efficiency_rating = Column(String(30), nullable=False)
    modular = Column(String(10), nullable=True)
    is_fanless = Column(Boolean, nullable=True)
    fan_size_mm = Column(Integer, nullable=True)
    pcie_8pin_connectors = Column(Integer, nullable=True)
    pcie_12pin_connectors = Column(Integer, nullable=True)
    pcie_16pin_connectors = Column(Integer, nullable=True)
    eps_connectors = Column(Integer, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    variants = relationship("PSU", back_populates="group")


class RAMGroup(Base):
    __tablename__ = "ram_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(150), nullable=False)  # e.g. "DDR5-6000 CL30 32GB (2x16)"

    # Street price lives on the group (shared by every kit in this spec).
    street_price_cents = Column(Integer, nullable=True)
    price_source = Column(String(20), nullable=True)
    last_price_checked_at = Column(DateTime(timezone=True), nullable=True)

    ddr_generation = Column(String(10), nullable=False)
    speed_mhz = Column(Integer, nullable=False)
    capacity_gb = Column(Integer, nullable=False)
    modules = Column(Integer, nullable=False)
    module_capacity_gb = Column(Integer, nullable=True)
    cas_latency = Column(Integer, nullable=True)
    voltage = Column(Float, nullable=True)
    is_ecc = Column(Boolean, nullable=True)
    # MemoryModuleType value. Backfilled to 'udimm' for every pre-existing kit
    # (all consumer); a server build filters on this, not on is_ecc.
    module_type = Column(String(10), nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    variants = relationship("RAMKit", back_populates="group")


class StorageGroup(Base):
    __tablename__ = "storage_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(150), nullable=False)  # e.g. "2TB Gen4 NVMe (7000/6900 MB/s)"

    # Street price lives on the group (shared by every drive in this spec).
    street_price_cents = Column(Integer, nullable=True)
    price_source = Column(String(20), nullable=True)
    last_price_checked_at = Column(DateTime(timezone=True), nullable=True)

    storage_type = Column(String(20), nullable=False)
    form_factor = Column(String(20), nullable=False)
    interface = Column(String(20), nullable=False)
    capacity_gb = Column(Integer, nullable=False)
    read_speed_mbps = Column(Integer, nullable=True)
    write_speed_mbps = Column(Integer, nullable=True)
    has_dram_cache = Column(Boolean, nullable=True)
    endurance_tbw = Column(Integer, nullable=True)
    rpm = Column(Integer, nullable=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    variants = relationship("StorageDrive", back_populates="group")


class CPU(PCPart):
    __tablename__ = "cpus"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability Requirements
    brand = Column(String(20), nullable=False)
    socket = Column(String(30), nullable=False)
    tdp_watts = Column(Integer, nullable=False)
    has_igpu = Column(Boolean, nullable=False)
    ddr_generation = Column(ARRAY(String), nullable=False)
    supported_features = Column(
        ARRAY(String), nullable=True
    )  # e.g. ["avx2", "sse4.2", "avx512"] (for certain games)

    # Validated in benchmarks.py
    benchmark_scores = Column(
        JSONB,
        nullable=True,
        doc='e.g. {"cinebench_r24_single": 2150, "cinebench_r24_multi": 14500, ...}',
    )

    # Other
    cores = Column(Integer, nullable=False)
    threads = Column(Integer, nullable=False)
    base_clock_ghz = Column(Float, nullable=True)
    boost_clock_ghz = Column(Float, nullable=True)
    l3_cache_mb = Column(Integer, nullable=True)
    pcie_generation = Column(Integer, nullable=True)
    max_memory_gb = Column(Integer, nullable=True)
    series = Column(String(100), nullable=True)

    # Server/workstation platform spec. These are what separate a Threadripper
    # or Xeon from a desktop part — not clocks. All nullable: a value of NULL
    # means "not recorded", which is different from a definitive zero/false.
    pcie_lanes = Column(
        Integer,
        nullable=True,
        doc="CPU-provided PCIe lanes (Threadripper 7970X=88, Ryzen 9800X3D=28). "
        "The hard ceiling on multi-GPU width regardless of board slot count.",
    )
    memory_channels = Column(
        Integer,
        nullable=True,
        doc="Memory channels (desktop=2, Threadripper=4, TR PRO/EPYC=8/12). "
        "Bandwidth is the binding constraint for HPC and CPU-side inference.",
    )
    supports_ecc = Column(Boolean, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "brand": self.brand,
            "cores": self.cores,
            "threads": self.threads,
            "base_clock_ghz": self.base_clock_ghz,
            "boost_clock_ghz": self.boost_clock_ghz,
            "tdp_w": self.tdp_watts,
            "socket": self.socket,
            "ddr_gen": self.ddr_generation,
            "has_integrated_graphics": self.has_igpu,
            "pcie_lanes": self.pcie_lanes,
            "memory_channels": self.memory_channels,
            "supports_ecc": self.supports_ecc,
        }

    __mapper_args__ = {"polymorphic_identity": "cpu"}


class CPUCooler(PCPart):
    __tablename__ = "cpu_coolers"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability Requirements
    supported_sockets = Column(ARRAY(String), nullable=False)
    cooler_type = Column(String(20), nullable=False)
    max_tdp_watts = Column(Integer, nullable=True)
    height_mm = Column(Integer, nullable=True)  # For air
    radiator_size_mm = Column(Integer, nullable=True)  # For liquid

    # Other
    fan_count = Column(Integer, nullable=True)
    fan_size_mm = Column(Integer, nullable=True)
    noise_dba = Column(Float, nullable=True)
    has_rgb = Column(Boolean, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "type": self.cooler_type,
            "max_tdp_w": self.max_tdp_watts,
            "noise_db": self.noise_dba,
            "height_mm": self.height_mm,
        }

    __mapper_args__ = {"polymorphic_identity": "cpucooler"}


class Motherboard(PCPart):
    __tablename__ = "motherboards"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability Requirements
    socket = Column(String(30), nullable=False)
    form_factor = Column(String(10), nullable=False)
    ddr_generation = Column(String(10), nullable=False)
    memory_slots = Column(Integer, nullable=False)
    has_wifi = Column(Boolean, nullable=False)  # User requirement
    m2_slots = Column(Integer, nullable=True)
    m2_pcie_gen = Column(Integer, nullable=True)

    # Other
    chipset = Column(String(30), nullable=True)
    max_memory_gb = Column(Integer, nullable=True)
    sata_ports = Column(Integer, nullable=True)
    pcie_x16_slots = Column(Integer, nullable=True)
    pcie_generation = Column(Integer, nullable=True)
    has_bluetooth = Column(Boolean, nullable=True)
    usb_type_a_count = Column(Integer, nullable=True)
    usb_type_c_count = Column(Integer, nullable=True)
    audio_codec = Column(String(50), nullable=True)

    # Server/workstation board spec — the board half of CPU.pcie_lanes etc.
    supports_ecc = Column(Boolean, nullable=True)
    has_ipmi = Column(
        Boolean,
        nullable=True,
        doc="Out-of-band management (IPMI/BMC). The clearest single signal "
        "that a board is a server board rather than a workstation board.",
    )
    memory_channels = Column(
        Integer,
        nullable=True,
        doc="Channels the board wires up. memory_slots alone can't say how to "
        "populate 8 DIMMs across 4 vs 8 channels for full bandwidth.",
    )
    memory_module_types = Column(
        ARRAY(String),
        nullable=True,
        doc="MemoryModuleType values the board accepts. NULL = unconstrained; "
        "a populated list is what keeps a TRX50/WRX90 board from being paired "
        "with unbuffered kits it will not POST on.",
    )

    @property
    def specs(self) -> dict:
        return {
            "socket": self.socket,
            "chipset": self.chipset,
            "form_factor": self.form_factor,
            "ddr_gen": self.ddr_generation,
            "ram_slots": self.memory_slots,
            "m2_slots": self.m2_slots,
            "sata_ports": self.sata_ports,
            "has_wifi": self.has_wifi,
            "supports_ecc": self.supports_ecc,
            "has_ipmi": self.has_ipmi,
            "memory_channels": self.memory_channels,
            "memory_module_types": self.memory_module_types,
            "supports_bios_flashback": None,
            "vrm_quality": None,
        }

    __mapper_args__ = {"polymorphic_identity": "motherboard"}


class RAMKit(PCPart):
    __tablename__ = "ram_kits"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ram_group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ram_groups.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Per-SKU variant fields (intrinsic spec lives on RAMGroup)
    height_mm = Column(Integer, nullable=True)
    has_rgb = Column(Boolean, nullable=True)

    group = relationship("RAMGroup", back_populates="variants")

    @property
    def specs(self) -> dict:
        g = self.group
        return {
            "ddr_gen": g.ddr_generation if g else None,
            "capacity_gb": g.capacity_gb if g else None,
            "speed_mhz": g.speed_mhz if g else None,
            "kit_count": g.modules if g else None,
        }

    __mapper_args__ = {"polymorphic_identity": "ramkit"}


class StorageDrive(PCPart):
    __tablename__ = "storage_drives"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    storage_group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("storage_groups.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # All intrinsic spec lives on StorageGroup; a drive differs only by
    # product name / price / listings.
    group = relationship("StorageGroup", back_populates="variants")

    @property
    def specs(self) -> dict:
        g = self.group
        return {
            "interface": g.interface if g else None,
            "capacity_gb": g.capacity_gb if g else None,
            "seq_read_mbs": g.read_speed_mbps if g else None,
            "seq_write_mbs": g.write_speed_mbps if g else None,
        }

    __mapper_args__ = {"polymorphic_identity": "storagedrive"}


class GPU(PCPart):
    __tablename__ = "gpus"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    gpu_chipset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gpu_chipsets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Per-board variant fields (chip-intrinsic spec lives on GPUChipset)
    brand = Column(String(20), nullable=False)
    length_mm = Column(Integer, nullable=False)
    width_slots = Column(Float, nullable=True)
    pcie_power_pins = Column(String(50), nullable=True)
    display_outputs = Column(Text, nullable=True)
    hdmi_version = Column(Text, nullable=True)
    dp_version = Column(Text, nullable=True)

    chipset = relationship("GPUChipset", back_populates="variants")

    @property
    def specs(self) -> dict:
        c = self.chipset
        return {
            "brand": self.brand,
            "vram_gb": c.vram_gb if c else None,
            "tdp_w": c.tdp_watts if c else None,
            "length_mm": self.length_mm,
            "pcie_slots": self.width_slots,
        }

    __mapper_args__ = {"polymorphic_identity": "gpu"}


class PSU(PCPart):
    __tablename__ = "psus"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    psu_group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("psu_groups.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Per-SKU variant field (electrical spec lives on PSUGroup)
    depth_mm = Column(Integer, nullable=True)

    group = relationship("PSUGroup", back_populates="variants")

    @property
    def specs(self) -> dict:
        g = self.group
        return {
            "wattage": g.wattage if g else None,
            "efficiency": g.efficiency_rating if g else None,
            "modular": g.modular if g else None,
            "form_factor": g.form_factor if g else None,
        }

    __mapper_args__ = {"polymorphic_identity": "psu"}


class Case(PCPart):
    __tablename__ = "cases"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability
    supported_mobo_form_factors = Column(ARRAY(String), nullable=False)
    max_gpu_length_mm = Column(Integer, nullable=False)
    max_cooler_height_mm = Column(Integer, nullable=False)
    max_radiator_front_mm = Column(Integer, nullable=True)
    max_radiator_top_mm = Column(Integer, nullable=True)
    max_psu_length_mm = Column(Integer, nullable=True)
    included_fan_count = Column(Integer, nullable=True)
    chamber_count = Column(Integer, nullable=True)
    front_panel_mesh = Column(Boolean, nullable=True)
    color = Column(String(50), nullable=True)  # User preference

    # Other
    size = Column(String(20), nullable=False)
    drive_bays_35 = Column(Integer, nullable=True)
    drive_bays_25 = Column(Integer, nullable=True)
    max_fan_slots = Column(Integer, nullable=True)
    has_glass_panel = Column(Boolean, nullable=True)
    weight_kg = Column(Float, nullable=True)
    length_mm = Column(Integer, nullable=True)
    width_mm = Column(Integer, nullable=True)
    height_mm = Column(Integer, nullable=True)
    usb_front_type_a = Column(Integer, nullable=True)
    usb_front_type_c = Column(Integer, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "size": self.size,
            "supported_mobo_sizes": self.supported_mobo_form_factors,
            "max_gpu_length_mm": self.max_gpu_length_mm,
            "max_cooler_height_mm": self.max_cooler_height_mm,
            "fan_slots": self.max_fan_slots,
            "included_fans": self.included_fan_count,
        }

    __mapper_args__ = {"polymorphic_identity": "case"}


class Fan(PCPart):
    __tablename__ = "fans"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability
    size_mm = Column(Integer, nullable=False)

    # Other
    max_rpm = Column(Integer, nullable=True)
    airflow_cfm = Column(Float, nullable=True)
    noise_dba = Column(Float, nullable=True)
    is_pwm = Column(Boolean, nullable=True)
    has_rgb = Column(Boolean, nullable=True)
    bearing_type = Column(String(30), nullable=True)
    is_static_pressure = Column(Boolean, nullable=True)
    pack_count = Column(Integer, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "size_mm": self.size_mm,
            "airflow_cfm": self.airflow_cfm,
            "noise_db": self.noise_dba,
            "pack_count": self.pack_count,
        }

    __mapper_args__ = {"polymorphic_identity": "fan"}
