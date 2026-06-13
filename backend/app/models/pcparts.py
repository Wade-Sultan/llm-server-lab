import uuid
import enum

from sqlalchemy import Boolean, Column, DateTime, String, Text, Integer, ForeignKey, Float
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
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
    used_market_viable = Column(Boolean, nullable=True, server_default="false")

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
    supported_features = Column(ARRAY(String), nullable=True) # e.g. ["avx2", "sse4.2", "avx512"] (for certain games)

    # Validated in benchmarks.py
    benchmark_scores = Column(
        JSONB, nullable=True,
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
    height_mm = Column(Integer, nullable=True) # For air
    radiator_size_mm = Column(Integer, nullable=True) # For liquid

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
    has_wifi = Column(Boolean, nullable=False) # User requirement
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
            "supports_bios_flashback": None,
            "vrm_quality": None,
        }

    __mapper_args__ = {"polymorphic_identity": "motherboard"}


class RAM(PCPart):
    __tablename__ = "ram"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability Requirements
    ddr_generation = Column(String(10), nullable=False)
    speed_mhz = Column(Integer, nullable=False)
    modules = Column(Integer, nullable=False)
    capacity_gb = Column(Integer, nullable=False)
    height_mm = Column(Integer, nullable=True)  

    # Other
    module_capacity_gb = Column(Integer, nullable=True)
    cas_latency = Column(Integer, nullable=True)
    voltage = Column(Float, nullable=True)
    has_rgb = Column(Boolean, nullable=True)
    is_ecc = Column(Boolean, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "ddr_gen": self.ddr_generation,
            "capacity_gb": self.capacity_gb,
            "speed_mhz": self.speed_mhz,
            "kit_count": self.modules,
        }

    __mapper_args__ = {"polymorphic_identity": "ram"}


class Storage(PCPart):
    __tablename__ = "storage"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability Requirements
    storage_type = Column(String(20), nullable=False)
    form_factor = Column(String(20), nullable=False)
    interface = Column(String(20), nullable=False) 
    capacity_gb = Column(Integer, nullable=False) 

    # Other
    read_speed_mbps = Column(Integer, nullable=True)
    write_speed_mbps = Column(Integer, nullable=True)
    has_dram_cache = Column(Boolean, nullable=True)
    endurance_tbw = Column(Integer, nullable=True)
    rpm = Column(Integer, nullable=True)  

    @property
    def specs(self) -> dict:
        return {
            "interface": self.interface,
            "capacity_gb": self.capacity_gb,
            "seq_read_mbs": self.read_speed_mbps,
            "seq_write_mbs": self.write_speed_mbps,
        }

    __mapper_args__ = {"polymorphic_identity": "storage"}


class GPU(PCPart):
    __tablename__ = "gpus"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        primary_key=True,
    )

    # Compatability Requirements
    chipset = Column(String(50), nullable=False)
    brand = Column(String(20), nullable=False)
    vram_gb = Column(Integer, nullable=False)
    tdp_watts = Column(Integer, nullable=False)
    length_mm = Column(Integer, nullable=False)
    pcie_power_pins = Column(String(50), nullable=True)
    recommended_psu_watts = Column(Integer, nullable=True)
    supported_features = Column(ARRAY(String), nullable=True) # e.g. ["dx12_ultimate", "vulkan_rt", "nvenc", "mesh_shaders"] (for certain games)

    # Validated in benchmarks.py
    benchmark_scores = Column(
        JSONB, nullable=True,
        doc='e.g. {"timespy": 22400, "port_royal": 14200, "speed_way": 5800, ...}',
    )

    # Other
    vram_type = Column(String(20), nullable=True)
    width_slots = Column(Float, nullable=True)
    pcie_generation = Column(Integer, nullable=True)
    base_clock_mhz = Column(Integer, nullable=True)
    boost_clock_mhz = Column(Integer, nullable=True)
    has_ray_tracing = Column(Boolean, nullable=True)

    # Nvidia
    cuda_cores = Column(Integer, nullable=True)
    tensor_cores = Column(Integer, nullable=True)

    # AMD
    stream_processors = Column(Integer, nullable=True)
    matrix_cores = Column(Integer, nullable=True)

    display_outputs = Column(Text, nullable=True)
    hdmi_version = Column(Text, nullable=True)
    dp_version = Column(Text, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "brand": self.brand,
            "vram_gb": self.vram_gb,
            "tdp_w": self.tdp_watts,
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

    # Compatability Requirements
    wattage = Column(Integer, nullable=False)
    form_factor = Column(String(10), nullable=False)
    efficiency_rating = Column(String(30), nullable=False)
    pcie_8pin_connectors = Column(Integer, nullable=True) # Needed for GPU
    pcie_12pin_connectors = Column(Integer, nullable=True) # For the latest GPUs
    pcie_16pin_connectors = Column(Integer, nullable=True)
    depth_mm = Column(Integer, nullable=True) 

    # Other
    modular = Column(String(10), nullable=True)
    eps_connectors = Column(Integer, nullable=True)
    fan_size_mm = Column(Integer, nullable=True)
    is_fanless = Column(Boolean, nullable=True)

    @property
    def specs(self) -> dict:
        return {
            "wattage": self.wattage,
            "efficiency": self.efficiency_rating,
            "modular": self.modular,
            "form_factor": self.form_factor,
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
    chamber_count= Column(Integer, nullable=True)
    front_panel_mesh = Column(Boolean, nullable=True)
    color = Column(String(50), nullable=True) # User preference

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
