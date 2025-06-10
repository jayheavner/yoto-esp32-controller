import QtQuick 6.7

Window {
    width: 480
    height: 800
    visible: true
    color: "#111"
    title: "Yoto Card Library"

    GridView {
        anchors.fill: parent
        anchors.margins: 10
        cellWidth: 150
        cellHeight: 200
        model: imageModel

        delegate: Image {
            width: 140
            height: 190
            source: imagePath
            fillMode: Image.PreserveAspectFit
        }
    }
}
