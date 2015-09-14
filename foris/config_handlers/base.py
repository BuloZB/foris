# Foris - web administration interface for OpenWrt based on NETCONF
# Copyright (C) 2013 CZ.NIC, z.s.p.o. <http://www.nic.cz>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import logging
import re

import bottle

from foris import gettext_dummy as gettext, ugettext as _
from foris.form import File, Password, Textbox, Dropdown, Checkbox, Hidden, Radio, Number, Email, Time, \
    MultiCheckbox
from foris import fapi
from foris.nuci import client, filters
from foris.nuci.filters import create_config_filter
from foris.nuci.modules.uci_raw import Uci, Config, Section, Option, List, Value, parse_uci_bool
from foris import validators


logger = logging.getLogger(__name__)


class BaseConfigHandler(object):
    def __init__(self, data=None):
        self.data = data
        self.__form_cache = None

    @property
    def form(self):
        if self.__form_cache is None:
            self.__form_cache = self.get_form()
        return self.__form_cache

    def get_form(self):
        """Get form for this wizard. MUST be a single-section form.

        :return:
        :rtype: fapi.ForisForm
        """
        raise NotImplementedError()

    def save(self, extra_callbacks=None):
        """

        :param extra_callbacks: list of extra callbacks to call when saved
        :return:
        """
        form = self.form
        form.validate()
        if extra_callbacks:
            for cb in extra_callbacks:
                form.add_callback(cb)
        if form.valid:
            form.save()
            return True
        else:
            return False


class PasswordHandler(BaseConfigHandler):
    """
    Setting the password
    """

    userfriendly_title = gettext("Password")

    def __init__(self, *args, **kwargs):
        self.change = kwargs.pop("change", False)
        super(PasswordHandler, self).__init__(*args, **kwargs)

    def get_form(self):
        # form definitions
        pw_form = fapi.ForisForm("password", self.data)
        pw_main = pw_form.add_section(name="set_password", title=_(self.userfriendly_title),
                                      description=_("Set your password for this administration "
                                                    "interface. The password must be at least 6 "
                                                    "characters long."))
        if self.change:
            pw_main.add_field(Password, name="old_password", label=_("Current password"))
            label_pass1 = _("New password")
            label_pass2 = _("New password (repeat)")
        else:
            label_pass1 = _("Password")
            label_pass2 = _("Password (repeat)")

        pw_main.add_field(Password, name="password", label=label_pass1, required=True,
                          validators=validators.LenRange(6, 128))
        pw_main.add_field(Password, name="password_validation", label=label_pass2,
                          required=True,
                          validators=validators.EqualTo("password", "password_validation",
                                                        _("Passwords are not equal.")))
        pw_main.add_field(Checkbox, name="set_system_pw",
                          label=_("Use the same password for advanced configuration"),
                          hint=_("Same password would be used for accessing this administration "
                                 "interface, for root user in LuCI web interface and for SSH "
                                 "login. Use a strong password! (If you choose not to set the "
                                 "password for advanced configuration here, you will have the "
                                 "option to do so later. Until then, the root account will be "
                                 "blocked.)"))

        def pw_form_cb(data):
            from beaker.crypto import pbkdf2
            if self.change:
                # if changing password, check the old pw is right first
                uci_data = client.get(filter=filters.foris_config)
                password_hash = uci_data.find_child("uci.foris.auth.password")
                # allow changing the password if password_hash is empty
                if password_hash:
                    password_hash = password_hash.value
                    # crypt automatically extracts salt and iterations from formatted pw hash
                    if password_hash != pbkdf2.crypt(data['old_password'], salt=password_hash):
                        return "save_result", {'wrong_old_password': True}

            uci = Uci()
            foris = Config("foris")
            uci.add(foris)
            auth = Section("auth", "config")
            foris.add(auth)
            # use 48bit pseudo-random salt internally generated by pbkdf2
            new_password_hash = pbkdf2.crypt(data['password'], iterations=1000)
            auth.add(Option("password", new_password_hash))

            if data['set_system_pw'] is True:
                client.set_password("root", data['password'])

            return "edit_config", uci

        pw_form.add_callback(pw_form_cb)
        return pw_form


class WanHandler(BaseConfigHandler):
    userfriendly_title = gettext("WAN")

    def get_form(self):
        # WAN
        wan_form = fapi.ForisForm("wan", self.data,
                                  filter=create_config_filter("network", "smrtd", "ucollect"))
        wan_main = wan_form.add_section(
            name="set_wan",
            title=_(self.userfriendly_title),
            description=_("Here you specify your WAN port settings. Usually, you can leave this "
                          "options untouched unless instructed otherwise by your internet service "
                          "provider. Also, in case there is a cable or DSL modem connecting your "
                          "router to the network, it is usually not necessary to change this "
                          "setting."))
        WAN_DHCP = "dhcp"
        WAN_STATIC = "static"
        WAN_PPPOE = "pppoe"
        WAN_OPTIONS = (
            (WAN_DHCP, _("DHCP (automatic configuration)")),
            (WAN_STATIC, _("Static IP address (manual configuration)")),
            (WAN_PPPOE, _("PPPoE (for DSL bridges, Modem Turris, etc.)")),
        )

        # protocol
        wan_main.add_field(Dropdown, name="proto", label=_("Protocol"),
                           nuci_path="uci.network.wan.proto",
                           args=WAN_OPTIONS, default=WAN_DHCP)

        # static ipv4
        wan_main.add_field(Textbox, name="ipaddr", label=_("IP address"),
                           nuci_path="uci.network.wan.ipaddr",
                           required=True, validators=validators.IPv4())\
            .requires("proto", WAN_STATIC)
        wan_main.add_field(Textbox, name="netmask", label=_("Network mask"),
                           nuci_path="uci.network.wan.netmask",
                           required=True, validators=validators.IPv4Netmask())\
            .requires("proto", WAN_STATIC)
        wan_main.add_field(Textbox, name="gateway", label=_("Gateway"),
                           nuci_path="uci.network.wan.gateway",
                           validators=validators.IPv4(),
                           required=True)\
            .requires("proto", WAN_STATIC)

        def extract_dns_item(dns_string, index, default=None):
            try:
                return dns_string.split(" ")[index]
            except IndexError:
                return default

        # DNS servers
        wan_main.add_field(Textbox, name="dns1", label=_("DNS server 1"),
                           nuci_path="uci.network.wan.dns",
                           nuci_preproc=lambda val: extract_dns_item(val.value, 0),
                           validators=validators.AnyIP(),
                           hint=_("DNS server address is not required as the built-in "
                                  "DNS resolver is capable of working without it."))\
            .requires("proto", WAN_STATIC)
        wan_main.add_field(Textbox, name="dns2", label=_("DNS server 2"),
                           nuci_path="uci.network.wan.dns",
                           nuci_preproc=lambda val: extract_dns_item(val.value, 1),
                           validators=validators.AnyIP(),
                           hint=_("DNS server address is not required as the built-in "
                                  "DNS resolver is capable of working without it."))\
            .requires("proto", WAN_STATIC)

        # static ipv6
        wan_main.add_field(Checkbox, name="static_ipv6", label=_("Use IPv6"),
                           nuci_path="uci.network.wan.ip6addr",
                           nuci_preproc=lambda val: bool(val.value))\
            .requires("proto", WAN_STATIC)
        wan_main.add_field(Textbox, name="ip6addr", label=_("IPv6 address"),
                           nuci_path="uci.network.wan.ip6addr",
                           validators=validators.IPv6Prefix(),
                           hint=_("IPv6 address and prefix length for WAN interface, "
                                  "e.g. 2001:db8:be13:37da::1/64"),
                           required=True)\
            .requires("proto", WAN_STATIC)\
            .requires("static_ipv6", True)
        wan_main.add_field(Textbox, name="ip6gw", label=_("IPv6 gateway"),
                           validators=validators.IPv6(),
                           nuci_path="uci.network.wan.ip6gw")\
            .requires("proto", WAN_STATIC)\
            .requires("static_ipv6", True)
        wan_main.add_field(Textbox, name="ip6prefix", label=_("IPv6 prefix"),
                           validators=validators.IPv6Prefix(),
                           nuci_path="uci.network.wan.ip6prefix",
                           hint=_("Address range for local network, "
                                  "e.g. 2001:db8:be13:37da::/64"))\
            .requires("proto", WAN_STATIC)\
            .requires("static_ipv6", True)

        # xDSL settings
        wan_main.add_field(Textbox, name="username", label=_("PAP/CHAP username"),
                           nuci_path="uci.network.wan.username")\
            .requires("proto", WAN_PPPOE)
        wan_main.add_field(Textbox, name="password", label=_("PAP/CHAP password"),
                           nuci_path="uci.network.wan.password")\
            .requires("proto", WAN_PPPOE)
        wan_main.add_field(Checkbox, name="ppp_ipv6", label=_("Enable IPv6"),
                           nuci_path="uci.network.wan.ipv6",
                           nuci_preproc=lambda val: bool(int(val.value)))\
            .requires("proto", WAN_PPPOE)

        # enable SMRT settings only if smrtd config is present
        has_smrtd = wan_form.nuci_config.find_child("uci.smrtd") is not None

        if has_smrtd:
            wan_main.add_field(Hidden, name="has_smrtd", default="1")
            wan_main.add_field(Checkbox, name="use_smrt", label=_("Use Modem Turris"),
                               nuci_path="uci.smrtd.global.enabled",
                               nuci_preproc=lambda val: bool(int(val.value)),
                               hint=_("Modem Turris (aka SMRT - Small Modem for Router Turris), "
                                      "a simple ADSL/VDSL modem designed specially for router "
                                      "Turris. Enable this option if you have Modem Turris "
                                      "connected to your router."))\
                .requires("proto", WAN_PPPOE)

            def get_smrtd_param(param_name):
                """Helper function for getting SMRTd params for "connections" list."""
                def wrapped(conn_list):
                    # internet connection must be always first list element
                    vlan_id, vpi, vci = (conn_list.children[0].content.split(" ")
                                         if conn_list else (None, None, None))
                    if param_name == "VPI":
                        return vpi
                    elif param_name == "VCI":
                        return vci
                    elif param_name == "VLAN":
                        return vlan_id
                    raise ValueError("Unknown SMRTd connection parameter.")
                return wrapped

            def get_smrtd_vlan(data):
                """Helper function for getting VLAN number from Uci data."""
                ifname = data.find_child("uci.network.wan.ifname")
                if ifname:
                    ifname = ifname.value
                    matches = re.match("eth2.(\d+)", ifname)
                    if matches:
                        return matches.group(1)

                connections = data.find_child("uci.smrtd.eth2.connections")
                result = get_smrtd_param("VLAN")(connections)
                return result

            # 802.1Q VLAN number is 12-bit, 0x0 and 0xFFF reserved
            wan_main.add_field(Textbox, name="smrt_vlan", label=_("xDSL VLAN number"),
                               nuci_preproc=get_smrtd_vlan,
                               validators=[validators.PositiveInteger(),
                                           validators.InRange(1, 4095)],
                               hint=_("VLAN number for your internet connection. Your ISP might "
                                      "have provided you this number. If you have VPI and VCI "
                                      "numbers instead, leave this field empty, a default value "
                                      "will be used automatically."))\
                .requires("use_smrt", True)

            vpi_vci_validator = validators.RequiredWithOtherFields(
                ("smrt_vpi", "smrt_vci"),
                _("Both VPI and VCI must be filled or both must be empty.")
            )

            wan_main.add_field(
                Textbox, name="smrt_vpi", label=_("VPI"),
                nuci_path="uci.smrtd.eth2.connections",
                nuci_preproc=get_smrtd_param("VPI"),
                validators=[validators.PositiveInteger(),
                            validators.InRange(0, 255),
                            vpi_vci_validator],
                hint=_("Virtual Path Identifier (VPI) is a parameter that you might have received "
                       "from your ISP. If you have a VLAN number instead, leave this field empty. "
                       "You need to fill in both VPI and VCI together.")
            ) \
                .requires("use_smrt", True)
            wan_main.add_field(
                Textbox, name="smrt_vci", label=_("VCI"),
                nuci_path="uci.smrtd.eth2.connections",
                nuci_preproc=get_smrtd_param("VCI"),
                validators=[validators.PositiveInteger(),
                            validators.InRange(32, 65535),
                            vpi_vci_validator],
                hint=_("Virtual Circuit Identifier (VCI) is a parameter that you might have "
                       "received from your ISP. If you have a VLAN number instead, leave this "
                       "field empty. You need to fill in both VPI and VCI together.")
            )\
                .requires("use_smrt", True)

        # custom MAC
        wan_main.add_field(Checkbox, name="custom_mac", label=_("Custom MAC address"),
                           nuci_path="uci.network.wan.macaddr",
                           nuci_preproc=lambda val: bool(val.value),
                           hint=_("Useful in cases, when a specific MAC address is required by "
                                  "your internet service provider."))

        wan_main.add_field(Textbox, name="macaddr", label=_("MAC address"),
                           nuci_path="uci.network.wan.macaddr",
                           validators=validators.MacAddress(),
                           hint=_("Separator is a colon, for example 00:11:22:33:44:55"),
                           required=True)\
            .requires("custom_mac", True)

        def wan_form_cb(data):
            uci = Uci()
            network = Config("network")
            uci.add(network)

            wan = Section("wan", "interface")
            network.add(wan)

            wan.add(Option("proto", data['proto']))
            if data['custom_mac'] is True:
                wan.add(Option("macaddr", data['macaddr']))
            else:
                wan.add_removal(Option("macaddr", None))

            ucollect_ifname = "eth2"

            if data['proto'] == WAN_PPPOE:
                wan.add(Option("username", data['username']))
                wan.add(Option("password", data['password']))
                wan.add(Option("ipv6", data['ppp_ipv6']))
                ucollect_ifname = "pppoe-wan"
            elif data['proto'] == WAN_STATIC:
                wan.add(Option("ipaddr", data['ipaddr']))
                wan.add(Option("netmask", data['netmask']))
                wan.add(Option("gateway", data['gateway']))
                dns_string = " ".join([data.get("dns1", ""), data.get("dns2", "")]).strip()
                wan.add(Option("dns", dns_string))
                if data.get("static_ipv6") is True:
                    wan.add(Option("ip6addr", data['ip6addr']))
                    wan.add(Option("ip6gw", data['ip6gw']))
                    wan.add(Option("ip6prefix", data['ip6prefix']))
                else:
                    wan.add_removal(Option("ip6addr", None))
                    wan.add_removal(Option("ip6gw", None))
                    wan.add_removal(Option("ip6prefix", None))

            if has_smrtd:
                smrtd = Config("smrtd")
                uci.add(smrtd)

                smrt_vlan = data.get("smrt_vlan")
                use_smrt = data.get("use_smrt", False)
                wan_ifname = "eth2"

                eth2 = Section("eth2", "interface")
                smrtd.add(eth2)
                eth2.add(Option("name", "eth2"))

                if use_smrt:
                    if not smrt_vlan:
                        # "proprietary" number - and also a common VLAN ID in CZ
                        smrt_vlan = "848"
                    wan_ifname += ".%s" % smrt_vlan

                vpi, vci = data.get("smrt_vpi"), data.get("smrt_vci")
                connections = List("connections")
                if vpi and vci:
                    eth2.add(connections)
                    connections.add(Value(1, "%s %s %s" % (smrt_vlan, vpi, vci)))
                elif use_smrt:
                    eth2.add_removal(connections)

                smrtd_global = Section("global", "global")
                smrtd.add(smrtd_global)
                smrtd_global.add(Option("enabled", use_smrt))

                # set correct ifname for WAN - must be changed when disabling SMRT
                wan.add(Option("ifname", wan_ifname))

            # set interface for ucollect to listen on
            interface_if_name = None
            ucollect_interface0 = wan_form.nuci_config.find_child("uci.ucollect.@interface[0]")
            if ucollect_interface0:
                interface_if_name = ucollect_interface0.name

            ucollect = Config("ucollect")
            uci.add(ucollect)
            interface = Section(interface_if_name, "interface", True)
            ucollect.add(interface)
            interface.add(Option("ifname", ucollect_ifname))

            return "edit_config", uci

        wan_form.add_callback(wan_form_cb)

        return wan_form


class DNSHandler(BaseConfigHandler):
    """
    DNS-related settings, currently for enabling/disabling upstream forwarding
    """

    userfriendly_title = gettext("DNS")

    def get_form(self):
        dns_form = fapi.ForisForm("dns", self.data,
                                  filter=create_config_filter("unbound"))
        dns_main = dns_form.add_section(name="set_dns",
                                        title=_(self.userfriendly_title))
        dns_main.add_field(Checkbox, name="forward_upstream", label=_("Use forwarding"),
                           nuci_path="uci.unbound.server.forward_upstream",
                           nuci_preproc=lambda val: bool(int(val.value)), default=True)

        def dns_form_cb(data):
            uci = Uci()
            unbound = Config("unbound")
            uci.add(unbound)
            server = Section("server", "unbound")
            unbound.add(server)
            server.add(Option("forward_upstream", data['forward_upstream']))
            return "edit_config", uci

        dns_form.add_callback(dns_form_cb)
        return dns_form


class TimeHandler(BaseConfigHandler):
    userfriendly_title = gettext("Time")

    def get_form(self):
        time_form = fapi.ForisForm("time", self.data, filter=filters.time)
        time_main = time_form.add_section(
            name="set_time",
            title=_(self.userfriendly_title),
            description=_("We could not synchronize the time with a timeserver, probably due to a "
                          "loss of connection. It is necessary for the router to have correct time "
                          "in order to function properly. Please, synchronize it with your "
                          "computer's time, or set it manually.")
        )

        time_main.add_field(Textbox, name="time", label=_("Time"), nuci_path="time",
                            nuci_preproc=lambda v: v.local)

        def time_form_cb(data):
            client.set_time(data['time'])
            return "none", None

        time_form.add_callback(time_form_cb)

        return time_form


class LanHandler(BaseConfigHandler):
    userfriendly_title = gettext("LAN")

    def get_form(self):
        lan_form = fapi.ForisForm("lan", self.data,
                                  filter=create_config_filter("dhcp", "network"))
        lan_main = lan_form.add_section(
            name="set_lan",
            title=_(self.userfriendly_title),
            description=_("This section contains settings for the local network (LAN). The provided"
                          " defaults are suitable for most networks. <br><strong>Note:</strong> If "
                          "you change the router IP address, all computers in LAN, probably "
                          "including the one you are using now, will need to obtain a <strong>new "
                          "IP address</strong> which does <strong>not</strong> happen <strong>"
                          "immediately</strong>. It is recommended to disconnect and reconnect all "
                          "LAN cables after submitting your changes to force the update. The next "
                          "page will not load until you obtain a new IP from DHCP (if DHCP enabled)"
                          " and you might need to <strong>refresh the page</strong> in your "
                          "browser.")
        )

        lan_main.add_field(Textbox, name="lan_ipaddr", label=_("Router IP address"),
                           nuci_path="uci.network.lan.ipaddr",
                           validators=validators.IPv4(),
                           hint=_("Router's IP address in inner network. Also defines the range of "
                                  "assigned IP addresses."))
        lan_main.add_field(Checkbox, name="dhcp_enabled", label=_("Enable DHCP"),
                           nuci_path="uci.dhcp.lan.ignore",
                           nuci_preproc=lambda val: not bool(int(val.value)), default=True,
                           hint=_("Enable this option to automatically assign IP addresses to "
                                  "the devices connected to the router."))
        lan_main.add_field(Textbox, name="dhcp_min", label=_("DHCP start"),
                           nuci_path="uci.dhcp.lan.start")\
            .requires("dhcp_enabled", True)
        lan_main.add_field(Textbox, name="dhcp_max", label=_("DHCP max leases"),
                           nuci_path="uci.dhcp.lan.limit")\
            .requires("dhcp_enabled", True)

        def lan_form_cb(data):
            uci = Uci()
            config = Config("dhcp")
            uci.add(config)

            dhcp = Section("lan", "dhcp")
            config.add(dhcp)
            # FIXME: this would overwrite any unrelated DHCP options the user might have set.
            # Maybe we should get the current values, scan them and remove selectively the ones
            # with 6 in front of them? Or have some support for higher level of stuff in nuci.
            options = List("dhcp_option")
            options.add(Value(0, "6," + data['lan_ipaddr']))
            dhcp.add_replace(options)
            network = Config("network")
            uci.add(network)
            interface = Section("lan", "interface")
            network.add(interface)
            interface.add(Option("ipaddr", data['lan_ipaddr']))
            if data['dhcp_enabled']:
                dhcp.add(Option("ignore", "0"))
                dhcp.add(Option("start", data['dhcp_min']))
                dhcp.add(Option("limit", data['dhcp_max']))
            else:
                dhcp.add(Option("ignore", "1"))

            return "edit_config", uci

        lan_form.add_callback(lan_form_cb)

        return lan_form


class WifiHandler(BaseConfigHandler):
    userfriendly_title = gettext("Wi-Fi")

    def get_form(self):
        stats = client.get(filter=filters.stats).find_child("stats")
        if len(stats.data['wireless-cards']) < 1:
            return None

        wifi_form = fapi.ForisForm("wifi", self.data,
                                   filter=create_config_filter("wireless"))
        wifi_main = wifi_form.add_section(
            name="set_wifi",
            title=_(self.userfriendly_title),
            description=_("If you want to use your router as a Wi-Fi access point, enable Wi-Fi "
                          "here and fill in an SSID (the name of the access point) and a "
                          "corresponding password. You can then set up your mobile devices, "
                          "using the QR code available next to the form.")
        )
        wifi_main.add_field(Hidden, name="iface_section", nuci_path="uci.wireless.@wifi-iface[0]",
                            nuci_preproc=lambda val: val.name)
        wifi_main.add_field(Checkbox, name="wifi_enabled", label=_("Enable Wi-Fi"), default=True,
                            nuci_path="uci.wireless.@wifi-iface[0].disabled",
                            nuci_preproc=lambda val: not bool(int(val.value)))
        wifi_main.add_field(Textbox, name="ssid", label=_("SSID"),
                            nuci_path="uci.wireless.@wifi-iface[0].ssid",
                            required=True, validators=validators.ByteLenRange(1, 32))\
            .requires("wifi_enabled", True)
        wifi_main.add_field(Checkbox, name="ssid_hidden", label=_("Hide SSID"), default=False,
                            nuci_path="uci.wireless.@wifi-iface[0].hidden",
                            hint=_("If set, network is not visible when scanning for available "
                                   "networks."))\
            .requires("wifi_enabled", True)

        channels_2g4 = [("auto", _("auto"))]
        channels_5g = []
        for channel in stats.data['wireless-cards'][0]['channels']:
            if channel['disabled']:
                continue
            pretty_channel = "%s (%s MHz)" % (channel['number'], channel['frequency'])
            if channel['frequency'] < 2500:
                channels_2g4.append((str(channel['number']), pretty_channel))
            else:
                channels_5g.append((str(channel['number']), pretty_channel))

        is_dual_band = False
        # hwmode choice for dual band devices
        if len(channels_2g4) > 1 and len(channels_5g) > 1:
            is_dual_band = True
            wifi_main.add_field(Radio, name="hwmode", label=_("Wi-Fi mode"), default="11ng",
                                args=(("11g", "2.4 GHz (g)"), ("11a", "5 GHz (a)")),
                                nuci_path="uci.wireless.radio0.hwmode",
                                nuci_preproc=lambda x: x.value.replace("n", ""),  # old configs used
                                                                                  # 11ng/11na
                                hint=_("The 2.4 GHz band is more widely supported by clients, but "
                                       "tends to have more interference. The 5 GHz band is a newer"
                                       " standard and may not be supported by all your devices. It "
                                       "usually has less interference, but the signal does not "
                                       "carry so well indoors."))\
                .requires("wifi_enabled", True)
        wifi_main.add_field(
            Dropdown, name="htmode", label=_("802.11n mode"),
            args=(("NOHT", _("Disabled")),
                  ("HT20", _("Enabled (20 MHz wide channel)")),
                  ("HT40", _("Enabled (40 MHz wide channel)"))),
            nuci_path="uci.wireless.radio0.htmode",
            hint=_("Change this to adjust 802.11n mode of operation. 802.11n with 40 MHz wide "
                   "channels can yield higher throughput but can cause more interference in the "
                   "network. If you don't know what to choose, use the default option with "
                   "20 MHz wide channel.")
        ).requires("wifi_enabled", True)
        # 2.4 GHz channels
        if len(channels_2g4) > 1:
            field_2g4 = wifi_main.add_field(Dropdown, name="channel2g4", label=_("Network channel"),
                                            default=channels_2g4[0][0], args=channels_2g4,
                                            nuci_path="uci.wireless.radio0.channel")
            if is_dual_band:
                field_2g4.requires("hwmode", "11g")
        # 5 GHz channels
        if len(channels_5g) > 1:
            field_5g = wifi_main.add_field(Dropdown, name="channel5g", label=_("Network channel"),
                                           default=channels_5g[0][0], args=channels_5g,
                                           nuci_path="uci.wireless.radio0.channel")
            if is_dual_band:
                field_5g.requires("hwmode", "11a")
        wifi_main.add_field(Password, name="key", label=_("Network password"),
                            nuci_path="uci.wireless.@wifi-iface[0].key",
                            required=True,
                            validators=validators.ByteLenRange(8, 63),
                            hint=_("WPA2 pre-shared key, that is required to connect to the "
                                   "network. Minimum length is 8 characters."))\
            .requires("wifi_enabled", True)

        def wifi_form_cb(data):
            uci = Uci()
            wireless = Config("wireless")
            uci.add(wireless)

            iface = Section(data['iface_section'], "wifi-iface")
            wireless.add(iface)
            device = Section("radio0", "wifi-device")
            wireless.add(device)
            # we must toggle both wifi-iface and device
            iface.add(Option("disabled", not data['wifi_enabled']))
            device.add(Option("disabled", not data['wifi_enabled']))
            if data['wifi_enabled']:
                iface.add(Option("ssid", data['ssid']))
                iface.add(Option("hidden", data['ssid_hidden']))
                iface.add(Option("encryption", "psk2+tkip+aes"))
                iface.add(Option("key", data['key']))
                if data.get('channel2g4'):
                    channel = data['channel2g4']
                elif data.get('channel5g'):
                    channel = data['channel5g']
                else:
                    logger.critical("Saving form without Wi-Fi channel: %s", data)
                    channel = "auto"
                hwmode = data.get('hwmode')
                if hwmode:
                    # change hwmode only if we had the choice
                    device.add(Option("hwmode", hwmode))
                device.add(Option("htmode", data['htmode']))
                # channel is in wifi-device section
                device.add(Option("channel", channel))
            else:
                pass  # wifi disabled

            return "edit_config", uci

        wifi_form.add_callback(wifi_form_cb)

        return wifi_form


class SystemPasswordHandler(BaseConfigHandler):
    """
    Setting the password of a system user (currently only root's pw).
    """

    userfriendly_title = gettext("Advanced administration")

    def get_form(self):
        system_pw_form = fapi.ForisForm("system_password", self.data)
        system_pw_main = system_pw_form.add_section(
            name="set_password",
            title=_(self.userfriendly_title),
            description=_("In order to access the advanced configuration possibilities which are "
                          "not present here, you must set the root user's password. The advanced "
                          "configuration options can be managed either through the "
                          "<a href=\"//%(host)s/%(path)s\">LuCI web interface</a> "
                          "or over SSH.") % {'host': bottle.request.get_header('host'),
                                             'path': 'cgi-bin/luci'}
        )
        system_pw_main.add_field(Password, name="password", label=_("Password"), required=True,
                                 validators=validators.LenRange(6, 128))
        system_pw_main.add_field(Password, name="password_validation", label=_("Password (repeat)"),
                                 required=True,
                                 validators=validators.EqualTo("password", "password_validation",
                                                               _("Passwords are not equal.")))

        def system_pw_form_cb(data):
            client.set_password("root", data["password"])
            return "none", None

        system_pw_form.add_callback(system_pw_form_cb)
        return system_pw_form


class MaintenanceHandler(BaseConfigHandler):
    userfriendly_title = gettext("Maintenance")

    def get_form(self):
        maintenance_form = fapi.ForisForm("maintenance", self.data)
        maintenance_main = maintenance_form.add_section(name="restore_backup",
                                                        title=_(self.userfriendly_title))
        maintenance_main.add_field(File, name="backup_file", label=_("Backup file"), required=True)

        def maintenance_form_cb(data):
            result = client.load_config_backup(data['backup_file'].file)
            return "save_result", {'new_ip': result}

        maintenance_form.add_callback(maintenance_form_cb)
        return maintenance_form


class NotificationsHandler(BaseConfigHandler):
    userfriendly_title = gettext("Notifications")

    def get_form(self):
        notifications_form = fapi.ForisForm("notifications", self.data,
                                            filter=create_config_filter("user_notify"))

        notifications = notifications_form.add_section(name="notifications",
                                                       title=_("Notifications settings"))
        # notifications settings
        notifications.add_field(Checkbox, name="enable_smtp", label=_("Enable notifications"),
                                nuci_path="uci.user_notify.smtp.enable",
                                nuci_preproc=lambda val: bool(int(val.value)),
                                default=False)

        notifications.add_field(
            Radio,
            name="use_turris_smtp",
            label=_("SMTP provider"),
            default="0",
            args=(("1", _("Turris")), ("0", _("Custom"))),
            nuci_path="uci.user_notify.smtp.use_turris_smtp",
            hint=_("If you set SMTP provider to \"Turris\", the servers provided to members of the "
                   "Turris project would be used. These servers do not require any additional "
                   "settings. If you want to set your own SMTP server, please select \"Custom\" "
                   "and enter required settings."))\
            .requires("enable_smtp", True)

        notifications.add_field(
            Textbox,
            name="to",
            label=_("Recipient's email"),
            nuci_path="uci.user_notify.smtp.to",
            nuci_preproc=lambda x: " ".join(map(lambda value: value.content, x.children)),
            hint=_("Email address of recipient. Separate multiple addresses by spaces."),
            required=True
        ).requires("enable_smtp", True)

        # sender's name for CZ.NIC SMTP only
        notifications.add_field(
            Textbox,
            name="sender_name",
            label=_("Sender's name"),
            hint=_("Name of the sender - will be used as a part of the "
                   "sender's email address before the \"at\" sign."),
            nuci_path="uci.user_notify.smtp.sender_name",
            validators=[validators.RegExp(_("Sender's name can contain only "
                                            "alphanumeric characters, dots "
                                            "and underscores."),
                                          r"^[0-9a-zA-Z_\.-]+$")],
            required=True
        )\
            .requires("enable_smtp", True)\
            .requires("use_turris_smtp", "1")

        SEVERITY_OPTIONS = (
            (1, _("Reboot is required")),
            (2, _("Reboot or attention is required")),
            (3, _("Reboot or attention is required or update was installed")),
        )
        notifications.add_field(Dropdown, name="severity", label=_("Importance"),
                                nuci_path="uci.user_notify.notifications.severity",
                                nuci_preproc=lambda val: int(val.value),
                                args=SEVERITY_OPTIONS, default=1)\
            .requires("enable_smtp", True)
        notifications.add_field(Checkbox, name="news", label=_("Send news"),
                                hint=_("Send emails about new features."),
                                nuci_path="uci.user_notify.notifications.news",
                                nuci_preproc=lambda val: bool(int(val.value)),
                                default=False)\
            .requires("enable_smtp", True)

        # SMTP settings (custom server)
        smtp = notifications_form.add_section(name="smtp", title=_("SMTP settings"))
        smtp.add_field(Email, name="from", label=_("Sender address (From)"),
                       hint=_("This is the address notifications are send from."),
                       nuci_path="uci.user_notify.smtp.from",
                       required=True)\
            .requires("enable_smtp", True)\
            .requires("use_turris_smtp", "0")
        smtp.add_field(Textbox, name="server", label=_("Server address"),
                                nuci_path="uci.user_notify.smtp.server",
                                required=True)\
            .requires("enable_smtp", True)\
            .requires("use_turris_smtp", "0")
        smtp.add_field(Number, name="port", label=_("Server port"),
                       nuci_path="uci.user_notify.smtp.port",
                       validators=[validators.PositiveInteger()],
                       required=True) \
            .requires("enable_smtp", True)\
            .requires("use_turris_smtp", "0")

        SECURITY_OPTIONS = (
            ("none", _("None")),
            ("ssl", _("SSL/TLS")),
            ("starttls", _("STARTTLS")),
        )
        smtp.add_field(Dropdown, name="security", label=_("Security"),
                       nuci_path="uci.user_notify.smtp.security",
                       args=SECURITY_OPTIONS, default="none") \
            .requires("enable_smtp", True).requires("use_turris_smtp", "0")

        smtp.add_field(Textbox, name="username", label=_("Username"),
                       nuci_path="uci.user_notify.smtp.username")\
            .requires("enable_smtp", True)\
            .requires("use_turris_smtp", "0")
        smtp.add_field(Password, name="password", label=_("Password"),
                       nuci_path="uci.user_notify.smtp.password")\
            .requires("enable_smtp", True)\
            .requires("use_turris_smtp", "0")

        # reboot time
        reboot = notifications_form.add_section(name="reboot",
                                                title=_("Automatic restarts"))
        reboot.add_field(Number, name="delay", label=_("Delay (days)"),
                         hint=_("Number of days that must pass between receiving the request "
                                "for restart and the automatic restart itself."),
                         nuci_path="uci.user_notify.reboot.delay",
                         validators=[validators.PositiveInteger(),
                                     validators.InRange(0, 10)],
                         required=True)
        reboot.add_field(Time, name="reboot_time", label=_("Reboot time"),
                         hint=_("Time of day of automatic reboot in HH:MM format."),
                         nuci_path="uci.user_notify.reboot.time",
                         validators=[validators.Time()],
                         required=True)

        def notifications_form_cb(data):
            uci = Uci()
            user_notify = Config("user_notify")
            uci.add(user_notify)

            smtp = Section("smtp", "smtp")
            user_notify.add(smtp)
            smtp.add(Option("enable", data['enable_smtp']))

            reboot = Section("reboot", "reboot")
            user_notify.add(reboot)
            reboot.add(Option("time", data['reboot_time']))
            reboot.add(Option("delay", data['delay']))

            if data['enable_smtp']:
                smtp.add(Option("use_turris_smtp", data['use_turris_smtp']))
                if data['use_turris_smtp'] == "0":
                    smtp.add(Option("server", data['server']))
                    smtp.add(Option("port", data['port']))
                    smtp.add(Option("username", data['username']))
                    smtp.add(Option("password", data['password']))
                    smtp.add(Option("security", data['security']))
                    smtp.add(Option("from", data['from']))
                else:
                    smtp.add(Option("sender_name", data['sender_name']))
                to = List("to")
                for i, to_item in enumerate(data['to'].split(" ")):
                    if to_item:
                        to.add(Value(i, to_item))
                smtp.add_replace(to)
                # notifications section
                notifications = Section("notifications", "notifications")
                user_notify.add(notifications)
                notifications.add(Option("severity", data['severity']))
                notifications.add(Option("news", data['news']))

            return "edit_config", uci

        notifications_form.add_callback(notifications_form_cb)

        return notifications_form


class UpdaterHandler(BaseConfigHandler):
    userfriendly_title = gettext("Updater")

    def get_form(self):
        pkg_list = client.get(filter=filters.updater).find_child("updater").pkg_list

        package_lists_form = fapi.ForisForm("package_lists", self.data,
                                            filter=create_config_filter("updater"))
        package_lists_main = package_lists_form.add_section(
            name="select_package_lists",
            title=_(self.userfriendly_title),
            description=_("Updater is a service that keeps all TurrisOS "
                          "software up to date. Apart from the standard "
                          "installation, you can optionally select lists of "
                          "additional software that'd be installed on the "
                          "router. This software can be selected from the "
                          "following list. "
                          "Please note that only software that is part of "
                          "TurrisOS or that has been installed from a package "
                          "list is maintained by Updater. Software that has "
                          "been installed manually or using opkg is not "
                          "affected.")
        )

        def make_preproc(list_name):
            """Make function for preprocessing value of single pkglist."""
            def preproc(list):
                enabled_names = map(lambda x: x.content, list.children)
                return list_name in enabled_names
            return preproc

        for pkg_list_item in pkg_list:
            package_lists_main.add_field(
                Checkbox,
                name="install_%s" % pkg_list_item.name,
                label=pkg_list_item.title,
                hint=pkg_list_item.description,
                nuci_path="uci.updater.pkglists.lists",
                nuci_preproc=make_preproc(pkg_list_item.name)
            )

        def package_lists_form_cb(data):
            uci = Uci()
            updater = Config("updater")
            uci.add(updater)

            pkglists = Section("pkglists", "pkglists")
            updater.add(pkglists)
            lists = List("lists")

            # create List with selected packages
            i = 0
            for k, v in data.iteritems():
                if v and k.startswith("install_"):
                    lists.add(Value(i, k[8:]))
                    i += 1
            if i == 0:
                pkglists.add_removal(lists)
            else:
                pkglists.add_replace(lists)

            return "edit_config", uci

        def package_lists_run_updater_cb(data):
            logger.info("Checking for updates.")
            client.check_updates()
            return "none", None

        package_lists_form.add_callback(package_lists_form_cb)
        package_lists_form.add_callback(package_lists_run_updater_cb)
        return package_lists_form


class UcollectHandler(BaseConfigHandler):
    userfriendly_title = gettext("uCollect")

    def get_form(self):
        ucollect_form = fapi.ForisForm("ucollect", self.data,
                                       filter=filters.create_config_filter("ucollect"))
        fakes = ucollect_form.add_section(
            name="fakes",
            title=_("Emulated services"),
            description=_("One of uCollect's features is emulation of some commonly abused "
                          "services. If this function is enabled, uCollect is listening for "
                          "incoming connection attempts to these services. Enabling of the "
                          "emulated services has no effect if another service is already "
                          "listening on its default port (port numbers are listed below).")
        )

        SERVICES_OPTIONS = (
            ("23tcp", _("Telnet (23/TCP)")),
        )

        def get_enabled_services(disabled_list):
            disabled_services = map(lambda value: value.content, disabled_list.children)
            res = [x[0] for x in SERVICES_OPTIONS if x[0] not in disabled_services]
            return res

        fakes.add_field(
            MultiCheckbox,
            name="services",
            label=_("Emulated services"),
            args=SERVICES_OPTIONS,
            multifield=True,
            nuci_path="uci.ucollect.fakes.disable",
            nuci_preproc=get_enabled_services,
            default=[x[0] for x in SERVICES_OPTIONS]
        )

        fakes.add_field(
            Checkbox,
            name="log_credentials",
            label=_("Collect credentials"),
            hint=_("If this option is enabled, user names and passwords are collected "
                   "and sent to server in addition to the IP address of the client."),
            nuci_path="uci.ucollect.fakes.log_credentials",
            nuci_preproc=parse_uci_bool
        )

        def ucollect_form_cb(data):
            uci = Uci()
            ucollect = Config("ucollect")
            uci.add(ucollect)

            fakes = Section("fakes", "fakes")
            ucollect.add(fakes)

            disable = List("disable")

            disabled_services = [x[0] for x in SERVICES_OPTIONS
                                 if x[0] not in data['services']]
            for i, service in enumerate(disabled_services):
                disable.add(Value(i, service))

            if len(disabled_services):
                fakes.add_replace(disable)
            else:
                # TODO: workaround for Nuci bug #3984 - remove when fixed
                fakes_section = ucollect_form.nuci_config.find_child("uci.ucollect.fakes")
                if fakes_section:
                    fakes.add_removal(disable)

            fakes.add(Option("log_credentials", data['log_credentials']))

            return "edit_config", uci

        ucollect_form.add_callback(ucollect_form_cb)

        return ucollect_form