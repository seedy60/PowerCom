"""TeamTalk bitflag classes.
Copyright (C) 2016-2026 Doug Lee

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your
option) any later version.

This program is distributed in the hope that it will be useful, but
WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License along
with this program. If not, see <http://www.gnu.org/licenses/>.

"""

from bitflags import BitFlags

class LogEvents(BitFlags):
    """Event logging flags for a server. Last verified against TT 5.14.
    """
    flags = (
        # The first letter of each short name can't easily be unique, so they are blank.
        (0x00000001, "", "connect", "User connected"),
        (0x00000002, "", "disconnect", "User disconnected"),
        (0x00000004, "", "login", "User logged in"),
        (0x00000008, "", "logout", "User logged out"),
        (0x00000010, "", "loginFail", "User login failed"),
        (0x00000020, "", "timeout", "User timed out"),
        (0x00000040, "", "kick", "User kicked"),
        (0x00000080, "", "ban", "User banned"),
        (0x00000100, "", "unban", "User unbanned"),
        (0x00000200, "", "status", "User updated (status change)"),
        (0x00000400, "", "join", "User joined channel"),
        (0x00000800, "", "left", "User left channel"),
        (0x00001000, "", "move", "User moved"),
        (0x00002000, "", "msgPrivate", "User sent a private message"),
        (0x00004000, "", "msgCustom", "User sent a custom message"),
        (0x00008000, "", "msgChannel", "User sent a channel message"),
        (0x00010000, "", "msgBroadcast", "User sent a broadcast message"),
        (0x00020000, "", "chanCreate", "Channel created"),
        (0x00040000, "", "chanUpdate", "Channel updated"),
        (0x00080000, "", "chanRemove", "Channel removed"),
        (0x00100000, "", "fileUpload", "File uploaded"),
        (0x00200000, "", "fileDownload", "File downloaded"),
        (0x00400000, "", "fileDelete", "File deleted"),
        (0x00800000, "", "serverUpdate", "Server properties updated"),
        (0x01000000, "", "serverConfigSave", "Server configuration saved"),
    )
    # Default values per TeamTalk standards.
    default = 0x01ffffff  # all supported flags

class UserRights(BitFlags):
    """UserRights for an account. Last verified against TT 5.15.
    """
    flags = (
        # The first letter of each short name is unique, case mattering.
        (0x00000001, "l", "loginMultiple", "Can log in multiple times"),
        (0x00000002, "f", "findUsers", "Can see users in all channels"),
        (0x00000004, "t", "tempChannels", "Can create temporary channels"),
        (0x00000008, "p", "permChannels", "Can create/modify all channels"),
        (0x00000010, "B", "Broadcast", "Can broadcast text messages"),
        (0x00000020, "k", "kick", "Can kick users off the server"),
        (0x00000040, "b", "ban", "Can ban users from the server"),
        (0x00000080, "m", "move", "Can move users between channels"),
        (0x00000100, "o", "op", "Can make other users channel operators"),
        (0x00000200, "u", "upload", "Can upload files"),
        (0x00000400, "d", "download", "Can download files"),
        (0x00000800, "U", "UpdateProps", "Can update server properties"),
        (0x00001000, "v", "voice", "Can transmit voice data"),
        (0x00002000, "V", "Video", "Can transmit video data (webcam)"),
        (0x00004000, "D", "Desktop", "Can share out the desktop"),
        (0x00008000, "I", "Input", "Can send input to another user's desktop"),
        (0x00010000, "s", "streamAudio", "Can stream audio files"),
        (0x00020000, "S", "StreamVideo", "Can stream video files"),
        (0x00040000, "N", "nickLocked", "Nickname is locked against user changes"),
        (0x00080000, "T", "statusLocked", "Status is locked against user changes"),
        (0x00100000, "r", "record", "Can record audio in all channels"),
        (0x00200000, "h", "seeHidden", "Can see hidden channels"),
        (0x00400000, "P", "PM", "Can send private messages"),
        (0x00800000, "C", "CM", "Can send channel messages"),
    )
    # Default values per TeamTalk standards.
    default = 0x000ff607

class ChannelTypes(BitFlags):
    """Bits defining some properties of a channel; stored in its "type" field. Last updated from TT 5.7.0.5026.
    """
    flags = (
        (0x01, "P", "permanent", "Permanent channel (as opposed to temporary)"),
        (0x02, "x", "exclusive", "Exclusive (one transmitter at a time)"),
        (0x04, "c", "classroom", "Classroom channel"),
        (0x08, "o", "opOnly", "Only ops see and hear"),
        (0x10, "p", "pushToTalk", "Push to talk only"),
        (0x20, "n", "noRecord", "No recording allowed"),
        # Note: The following bit indicates a hidden channel for TeamTalk 5.7 starting in Alpha, which surfaced in late 2020;
        # however, this bit was first spotted by this author sometime between November 5, 2019 and January 21, 2020.
        # Unfortunately there is no record of where this author spotted the bit that long ago though, so it's not possible to determine if its usage was legit or what it was for.
        # The above information comes from the creation of the following ToDo entry between the above dates:
        # Find meaning of channel bit 0x40.
        (0x40, "h", "hidden", "Hidden channel"),
    )
    # No default value but it would be 0 (temp) or 1 (perm); other bits default to 0 on channel creation.

class Subscriptions(BitFlags):
    """Bit flags comprising both subscriptions and intercepts, local and remote for each user.
    Last checked against TeamTalk 5.14.
    """
    flags = (
        (0x00000001, "u", "userMsg", "user message subscription"),
        (0x00000002, "c", "channelMsg", "channel message subscription"),
        (0x00000004, "b", "broadcast", "broadcast message subscription"),
        (0x00000008, "t", "typing", "typing notification and custom message subscription"),
        (0x00000010, "a", "audio", "audio subscription"),
        (0x00000020, "v", "video", "video subscription"),
        (0x00000040, "d", "desktop", "desktop subscription"),
        (0x00000080, "i", "input", "desktop input subscription"),
        (0x00000100, "s", "mediaStream", "media stream subscription"),
        (0x00000200, "k", "bitK", "bit k subscription"),
        (0x00000400, "l", "bitL", "bit l subscription"),
        (0x00000800, "m", "bitM", "bit m subscription"),
        (0x00001000, "n", "bitN", "bit n subscription"),
        (0x00002000, "o", "bitO", "bit o subscription"),
        (0x00004000, "p", "bitP", "bit p subscription"),
        (0x00008000, "q", "bitQ", "bit q subscription"),
        (0x00010000, "U", "iUserMsg", "user message intercept"),
        (0x00020000, "C", "iChanMsg", "channel message intercept"),
        (0x00040000, "B", "iBroadcast", "broadcast message intercept (not used)"),
        (0x00080000, "T", "iTyping", "typing notification and custom message intercept"),
        (0x00100000, "A", "iAudio", "audio intercept"),
        (0x00200000, "V", "iVideo", "video intercept"),
        (0x00400000, "D", "iDesktop", "desktop intercept"),
        (0x00800000, "I", "iInput", "desktop input intercept (not used)"),
        (0x01000000, "S", "iMediaStream", "media stream intercept"),
        (0x02000000, "K", "iBitK", "bit k intercept"),
        (0x04000000, "L", "iBitL", "bit l intercept"),
        (0x08000000, "M", "iBitM", "bit m intercept"),
        (0x10000000, "N", "iBitN", "bit n intercept"),
        (0x20000000, "O", "iBitO", "bit o intercept"),
        (0x40000000, "P", "iBitP", "bit p intercept"),
        (0x80000000, "Q", "iBitQ", "bit q intercept"),
    )
    # Default values per TeamTalk standards.
    default = 0x0000017f  # ucbtavds
    # Note that avd (audio video desktop) may not be set by default for text clients in all cases.

class BanTypes(BitFlags):
    """Ban type flags, last updated from TeamTalk 5.15.
    NOTE: In TeamTalk source, this is an enum with values 0 (none), 1, 2, and 4;
    but 3 occurs in practice and the values are in effect flags.
    """
    flags = (
        (0x01, "c", "channel", "Ban only applies to this channel"),
        (0x02, "i", "IP", "IP address ban"),
        (0x04, "u", "user", "Username ban"),
    )
    # No default value but it would be 2 (server-level IP ban).

