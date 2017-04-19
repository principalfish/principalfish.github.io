var battleground = {
  incumbent : null,
  challenger : null,
  low : 0,
  high : 10,

  handle : function(id, value){
    id = id.substring(14)

    if (id == "incumbent"){
      battleground.incumbent = value;
    } else if (id == "challenger"){
      battleground.challenger = value
    } else if (id == "low") {
      if (!(isNaN(parseFloat(value)))){
          if (parseFloat(value) < 0){
            value = 0;
          }
          battleground.low = parseFloat(value);
      }
    } else if (id == "high"){
      if (!(isNaN(parseFloat(value)))){
        if (parseFloat(value) > 100){
          value = 100;
        }
          battleground.high = parseFloat(value);
      }
    }

    battleground.check();
  },

  check : function(){
    if (battleground.incumbent != null && battleground.challenger != null){
      if (battleground.incumbent != battleground.challenger){
          filters.reset();
          battleground.filter();
      }
    }
  },

  filter : function(){

    if (currentMap.name == "2015election"){
      var dataSet = currentMap.seatData;
    }  else {
      var dataSet = currentMap.previousSeatData;
    }

    $.each(currentMap.seatData, function(seat, data){
      if (dataSet[seat].seatInfo.current != battleground.incumbent){
        data.filtered = false;

      }  else {
        var incumbent = dataSet[seat].seatInfo.current;

        if (battleground.challenger in dataSet[seat].partyInfo){
          var incumbentVotes = dataSet[seat].partyInfo[incumbent].total;
          var challengerVotes = dataSet[seat].partyInfo[battleground.challenger].total;
          var totalVotes = dataSet[seat].seatInfo.turnout;
          var lead = 100 * ((incumbentVotes / totalVotes) - (challengerVotes / totalVotes));

          if (battleground.low <= lead  && lead <= battleground.high){

            data.filtered = true;
          } else {
            data.filtered = false;
          }
        } else {
          data.filtered = false;
        }
      }

      if (data.filtered == false) {
          filters.opacities[seat] = 0.05;
      } else {
        filters.opacities[seat] = 1;
      }

    });
    filters.display();
  }

};
